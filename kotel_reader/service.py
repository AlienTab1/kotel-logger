from __future__ import annotations

import json
import logging
import signal
import threading
import time
import uuid
from pathlib import Path

from filelock import FileLock

from .config import secret
from .http import Forbidden, InvalidCredentials, LoggedOut, RemoteError, Thematronic
from .model import iso, normalize, utcnow
from .mqtt import Publisher
from .postgres import DatabaseWorker
from .session import Session
from .storage import Store


class JsonLog(logging.Formatter):
    def format(self, record):
        return json.dumps({"time": iso(utcnow()), "level": record.levelname, "event": record.getMessage()})


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.stop = threading.Event()
        self.store = Store(cfg["data_dir"], cfg["queue"])
        self.session = Session(cfg["session"], self.store, utcnow())
        self.http = Thematronic(cfg["thematronic"], secret("THEMATRONIC_USERNAME", True), secret("THEMATRONIC_PASSWORD", True))
        self.mqtt = Publisher(cfg["mqtt"], cfg["signals"], cfg["thematronic"]["device_id"], self.stop)
        self.db = DatabaseWorker(cfg["postgres"], self.store, self.stop)
        self.counters = self.store.get("counters", {"reads": 0, "logins": 0, "errors": 0, "losses": 0})
        self.last_read = None
        self.next_poll = 0
        self.next_status = 0
        self.next_prune = 0
        self.read_failures = 0
        self.workers = []

    def event(self, name, **details):
        now = utcnow()
        item = {"sample_id": str(uuid.uuid4()), "device_id": self.cfg["thematronic"]["device_id"],
                "occurred_at": iso(now), "event": name, "details": details}
        if self.store.enqueue("event", item, now):
            logging.error("queue_capacity_loss")
        logging.info(name)

    def controls(self):
        commands = self.store.apply_commands()
        if commands:
            self.mqtt.invalidate()
            self.event("control_changed", action=commands[-1][0])
        until = self.store.get("paused_until", 0)
        paused = until is None or until > utcnow()
        if paused:
            self.session.state = "PAUSED"
            self.mqtt.invalidate()
        return paused

    def accept(self, result):
        raw, received = result
        sample = normalize(raw, self.cfg["thematronic"]["device_id"], self.cfg["signals"], received)
        dropped = self.store.enqueue("snapshot", sample, utcnow())
        if dropped:
            logging.error("queue_capacity_loss")
        self.mqtt.offer(sample)
        self.last_read = received
        self.session.last_success = received
        self.session.forbidden_count = 0
        self.session.due = self.session.due_mono = 0
        self.session.state = "POLLING"
        self.session.reason = "valid_read"
        self.store.set("next_attempt", 0)
        self.read_failures = 0
        self.counters["reads"] += 1
        previous = set(self.store.get("unknown_signals", []))
        unknown = previous | set(sample["unknown_signals"])
        if unknown != previous:
            self.store.set("unknown_signals", sorted(unknown))
            logging.info("new_unmapped_signals")

    def status(self):
        now = utcnow()
        self.store.set("status", {
            "heartbeat": now, "state": self.session.state, "reason": self.session.reason,
            "last_read": iso(self.last_read) if self.last_read else None,
            "last_login": iso(self.session.login_at) if self.session.login_at else None,
            "session_age_s": now - self.session.login_at if self.session.login_at else None,
            "expected_session_lifetime_s": self.cfg["session"]["expected_session_lifetime_s"],
            "expiry_confidence": "user_configured" if self.cfg["session"]["expected_session_lifetime_s"] else "unknown",
            "next_attempt": iso(self.session.due) if self.session.due else None,
            "paused_until": self.store.get("paused_until", 0),
            "postgres": self.db.status, "mqtt": self.mqtt.status, "queue": self.store.stats(),
            "telemetry_available": self.mqtt.current() is not None,
            "source_freshness": "unverified", "counters": self.counters,
            "workers_alive": all(worker.is_alive() for worker in self.workers),
        })
        self.store.set("counters", self.counters)

    def cycle(self):
        s, now = self.session, utcnow()
        if self.controls():
            return
        if s.failed:
            s.state = "AUTH_FAILED"
            return
        if s.waiting():
            s.state = "AUTH_COOLDOWN" if s.reason in ("expected_expiry", "early_or_unclassified_loss") else "RETRY_BACKOFF"
            return
        if time.monotonic() < self.next_poll:
            return
        login = not s.authenticated
        try:
            if login:
                s.state = "AUTHENTICATING"
                self.counters["logins"] += 1
                self.status()
                result = self.http.login()
                s.success(result[1])
                self.event("login_verified")
            else:
                s.state = "POLLING"
                if self.cfg["thematronic"]["mode"] == "reload_then_read":
                    self.http.reload()
                    # Interruptible delay: local pause and shutdown are checked before reading.
                    deadline = time.monotonic() + self.cfg["thematronic"]["reload_wait_s"]
                    while time.monotonic() < deadline:
                        if self.stop.wait(min(0.25, deadline - time.monotonic())) or self.controls():
                            return
                        if time.monotonic() >= self.next_status:
                            self.status()
                            self.next_status = time.monotonic() + 2
                if self.controls() or self.stop.is_set():
                    return
                result = self.http.read()
            if not self.controls() and not self.stop.is_set():
                self.accept(result)
        except InvalidCredentials:
            s.failed, s.authenticated = True, False
            s.state, s.reason = "AUTH_FAILED", "invalid_credentials"
            self.mqtt.invalidate()
            self.event("auth_failed")
        except LoggedOut:
            self.counters["losses"] += 1
            self.mqtt.invalidate()
            self.event("logout", **s.loss(utcnow()))
        except Forbidden:
            self.counters["errors"] += 1
            s.forbidden_count += 1
            if login:
                s.auth_retry(utcnow())
            elif s.forbidden_count >= 3:
                self.mqtt.invalidate()
                s.wait("RETRY_BACKOFF", self.cfg["session"]["ambiguous_403_pause_s"], utcnow(), "forbidden_unclassified")
            self.event("forbidden_unclassified")
        except RemoteError as error:
            self.counters["errors"] += 1
            if login:
                s.auth_retry(utcnow(), error.retry_after)
            else:
                self.read_failures += 1
                delay = max(error.retry_after, min(self.cfg["thematronic"]["poll_s"] * 2 ** min(self.read_failures - 1, 5), 300))
                s.wait("RETRY_BACKOFF", delay, utcnow(), str(error))
            self.event("http_error", reason=str(error))
        finally:
            # No overlapping requests or catch-up bursts, including after errors or slow responses.
            self.next_poll = time.monotonic() + self.cfg["thematronic"]["poll_s"]

    def run(self):
        for worker, enabled, name in ((self.db, self.cfg["postgres"]["enabled"], "postgres"),
                                      (self.mqtt, self.cfg["mqtt"]["enabled"], "mqtt")):
            if enabled:
                thread = threading.Thread(target=worker.run, name=name, daemon=True)
                self.workers.append(thread)
                thread.start()
        try:
            while not self.stop.is_set():
                if time.monotonic() >= self.next_status:
                    self.status()
                    self.next_status = time.monotonic() + 2
                if time.monotonic() >= self.next_prune:
                    if self.store.prune(utcnow()):
                        logging.error("queue_age_loss")
                    self.next_prune = time.monotonic() + 60
                if not all(worker.is_alive() for worker in self.workers):
                    raise RuntimeError("worker_stopped")
                self.cycle()
                self.stop.wait(0.25)
        finally:
            self.stop.set()
            self.mqtt.invalidate()
            for thread in self.workers:
                thread.join(timeout=30)
            self.session.state = "STOPPED"
            self.status()
            self.http.close()
            self.store.close()


def run(cfg):
    Path(cfg["data_dir"]).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(cfg["data_dir"]) / "collector.lock"), timeout=0):
        service = Service(cfg)
        signal.signal(signal.SIGTERM, lambda *_: service.stop.set())
        signal.signal(signal.SIGINT, lambda *_: service.stop.set())
        service.run()

from __future__ import annotations

import json
import uuid
import hashlib
import logging
import signal
import threading
import time
from pathlib import Path

from filelock import FileLock

from .config import secret
from .http import Forbidden, InvalidCredentials, LoggedOut, RemoteError, Thematronic
from .model import iso, normalize, utcnow
from .mqtt import Publisher
from .metrics import Metrics
from .session import Session
from .storage import Store
from .telemetry import Telemetry


class JsonLog(logging.Formatter):
    def format(self, record):
        return json.dumps({"time": iso(record.created), "level": record.levelname, "event": record.getMessage(),
                           "details": getattr(record, "details", {})})


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.run_id = uuid.uuid4().hex
        self.last_summary = 0
        self.previous_health = None
        self.payload_hash = None
        self.changed_at = time.monotonic()
        self.event_log = None
        self.stop = threading.Event()
        self.store = Store(cfg["data_dir"])
        self.session = Session(cfg["session"], self.store, utcnow())
        self.http = Thematronic(cfg["thematronic"], secret("THEMATRONIC_USERNAME", True), secret("THEMATRONIC_PASSWORD", True))
        self.telemetry = Telemetry(cfg["telemetry"]["stale_s"])
        self.mqtt = Publisher(cfg["mqtt"], cfg["signals"], cfg["thematronic"]["device_id"], self.stop, self.telemetry)
        self.metrics = Metrics(cfg["prometheus"], cfg["signals"], cfg["thematronic"]["device_id"], self.telemetry)
        self.counters = self.store.get("counters", {"reads": 0, "logins": 0, "errors": 0, "losses": 0})
        self.last_read = None
        self.last_http_error = self.store.get("last_http_error")
        self.next_poll = 0
        self.next_status = 0
        self.read_failures = 0
        self.workers = []

    def event(self, name, **details):
        # Fixed event/reason identifiers only; never log HTTP bodies or credentials.
        logging.info(name, extra={"details": {"run_id": self.run_id, "state": self.session.state, **details}})

    def record_http_error(self, error):
        self.last_http_error = {"time": iso(utcnow()), "reason": str(error), **error.details}
        self.store.set("last_http_error", self.last_http_error)

    def controls(self):
        commands = self.store.apply_commands()
        if commands:
            self.telemetry.invalidate()
            self.event("control_changed", action=commands[-1][0])
        until = self.store.get("paused_until", 0)
        paused = until is None or until > utcnow()
        if paused:
            self.session.state = "PAUSED"
            self.telemetry.invalidate()
        return paused

    def accept(self, result):
        raw, received = result
        if self.last_http_error:
            self.event("read_recovered", previous_reason=self.last_http_error["reason"],
                       gap_since_last_read_s=received - self.last_read if self.last_read else None)
        digest = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
        if digest != self.payload_hash:
            self.payload_hash, self.changed_at = digest, time.monotonic()
        sample = normalize(raw, self.cfg["thematronic"]["device_id"], self.cfg["signals"], received)
        # Overwrite one diagnostic snapshot; never reload it as live telemetry after restart.
        self.store.set("latest_snapshot", sample)
        self.telemetry.offer(sample)
        self.last_read = received
        self.last_http_error = None
        self.store.set("last_http_error", None)
        self.session.last_success = received
        self.session.forbidden_count = 0
        self.session.due = self.session.due_mono = 0
        self.session.state = "POLLING"
        self.session.reason = "valid_read"
        self.store.set("next_attempt", 0)
        self.read_failures = 0
        self.counters["reads"] += 1
        previous = set(self.store.get("unknown_signals", []))
        unknown = set(sample["unknown_signals"])
        if unknown != previous:
            self.store.set("unknown_signals", sorted(unknown))
            self.event("new_unmapped_signals", count=len(unknown))

    def status(self):
        now = utcnow()
        workers_alive = all(worker.is_alive() for worker in self.workers)
        self.metrics.update(self.session.state, self.counters, self.mqtt.status, workers_alive)
        health = (self.session.state, self.mqtt.status, self.telemetry.current() is not None)
        if health != self.previous_health:
            self.event("state_changed", mqtt=health[1], telemetry_available=health[2], reason=self.session.reason)
            self.previous_health = health
        if time.monotonic() - self.last_summary >= 60:
            self.event("collector_summary", counters=dict(self.counters), last_read=iso(self.last_read) if self.last_read else None,
                       unchanged_response_s=round(time.monotonic() - self.changed_at) if self.payload_hash else None,
                       source_freshness="unverified", next_attempt=iso(self.session.due) if self.session.due else None,
                       logging=self.event_log.snapshot() if self.event_log else {})
            self.last_summary = time.monotonic()
        self.store.set("status", {
            "logging": self.event_log.snapshot() if self.event_log else {},
            "heartbeat": now, "state": self.session.state, "reason": self.session.reason,
            "last_http_error": self.last_http_error,
            "last_read": iso(self.last_read) if self.last_read else None,
            "last_login": iso(self.session.login_at) if self.session.login_at else None,
            "session_age_s": now - self.session.login_at if self.session.login_at else None,
            "expected_session_lifetime_s": self.cfg["session"]["expected_session_lifetime_s"],
            "expiry_confidence": "user_configured" if self.cfg["session"]["expected_session_lifetime_s"] else "unknown",
            "next_attempt": iso(self.session.due) if self.session.due else None,
            "paused_until": self.store.get("paused_until", 0),
            "mqtt": self.mqtt.status,
            "metrics_listen": f"{self.cfg['prometheus']['host']}:{self.cfg['prometheus']['port']}",
            "telemetry_available": self.telemetry.current() is not None,
            "source_freshness": "unverified", "counters": self.counters,
            "workers_alive": workers_alive,
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
                self.event("login_started")
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
        except InvalidCredentials as error:
            self.record_http_error(error)
            self.counters["errors"] += 1
            s.failed, s.authenticated = True, False
            s.state, s.reason = "AUTH_FAILED", "invalid_credentials"
            self.telemetry.invalidate()
            self.event("auth_failed", **error.details)
        except LoggedOut as error:
            self.record_http_error(error)
            self.counters["losses"] += 1
            self.telemetry.invalidate()
            self.event("logout", **s.loss(utcnow()), **error.details)
        except Forbidden as error:
            self.record_http_error(error)
            self.counters["errors"] += 1
            s.forbidden_count += 1
            if login:
                s.auth_retry(utcnow())
            elif s.forbidden_count >= 3:
                self.telemetry.invalidate()
                s.wait("RETRY_BACKOFF", self.cfg["session"]["ambiguous_403_pause_s"], utcnow(), "forbidden_unclassified")
            self.event("forbidden_unclassified", **error.details)
        except RemoteError as error:
            self.record_http_error(error)
            self.counters["errors"] += 1
            if login:
                s.auth_retry(utcnow(), error.retry_after)
            else:
                self.read_failures += 1
                delay = max(error.retry_after, min(self.cfg["thematronic"]["poll_s"] * 2 ** min(self.read_failures - 1, 5), 300))
                s.wait("RETRY_BACKOFF", delay, utcnow(), str(error))
            self.event("http_error", reason=str(error), operation="login" if login else "read",
                       consecutive_read_failures=self.read_failures, next_attempt=iso(s.due) if s.due else None,
                       retry_s=max(0, s.due - utcnow()), **error.details)
        finally:
            # No overlapping requests or catch-up bursts, including after errors or slow responses.
            self.next_poll = time.monotonic() + self.cfg["thematronic"]["poll_s"]

    def run(self):
        try:
            from . import __version__
            self.event("collector_started", version=__version__, mode=self.cfg["thematronic"]["mode"])
            self.metrics.start()
            self.workers.append(self.metrics.thread)
            if self.cfg["mqtt"]["enabled"]:
                thread = threading.Thread(target=self.mqtt.run, name="mqtt", daemon=True)
                self.workers.append(thread)
                thread.start()
            while not self.stop.is_set():
                if time.monotonic() >= self.next_status:
                    self.status()
                    self.next_status = time.monotonic() + 2
                if not all(worker.is_alive() for worker in self.workers):
                    raise RuntimeError("worker_stopped")
                self.cycle()
                # Publish diagnostics after each cycle so scrapes see state transitions immediately.
                self.metrics.update(self.session.state, self.counters, self.mqtt.status)
                self.stop.wait(0.25)
        finally:
            self.stop.set()
            self.telemetry.invalidate()
            self.metrics.close()
            for thread in self.workers:
                thread.join(timeout=30)
            self.session.state = "STOPPED"
            self.status()
            self.event("collector_stopped")
            self.http.close()
            self.store.close()


def run(cfg):
    Path(cfg["data_dir"]).mkdir(parents=True, exist_ok=True)
    with FileLock(str(Path(cfg["data_dir"]) / "collector.lock"), timeout=0):
        service = Service(cfg)
        signal.signal(signal.SIGTERM, lambda *_: service.stop.set())
        signal.signal(signal.SIGINT, lambda *_: service.stop.set())
        from .eventlog import EventLog
        event_log = EventLog(cfg, JsonLog())
        service.event_log = event_log
        logging.getLogger().addHandler(event_log)
        try:
            service.run()
        finally:
            logging.getLogger().removeHandler(event_log)
            event_log.close()

from __future__ import annotations

import json
import uuid
import hashlib
import random
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
        self.session = Session(cfg["session"], self.store, utcnow(), cfg["retry"]["max_s"])
        self.http = Thematronic(cfg["thematronic"], secret("THEMATRONIC_USERNAME", True), secret("THEMATRONIC_PASSWORD", True), mapping=cfg["signals"])
        self.telemetry = Telemetry(cfg["telemetry"]["stale_s"])
        self.mqtt = Publisher(cfg["mqtt"], cfg["signals"], cfg["thematronic"]["device_id"], self.stop, self.telemetry)
        self.metrics = Metrics(cfg["prometheus"], cfg["signals"], cfg["thematronic"]["device_id"], self.telemetry)
        self.counters = self.store.get("counters", {"reads": 0, "logins": 0, "errors": 0, "losses": 0})
        self.last_read = self.store.get("last_valid_read")
        self.verifying = False
        self.failure_counts = self.store.get("failure_counts", {})
        self.validation_failures = self.store.get("consecutive_validation_failures", 0)
        self.error_totals = self.store.get("error_totals", {})
        self.outage_elapsed = self.store.get("outage_elapsed", 0)
        self.outage_tick = time.monotonic() if self.store.get("outage_active", False) else None
        self.last_http_error = self.store.get("last_http_error")
        self.next_poll = 0
        self.next_status = 0
        self.read_failures = self.store.get("consecutive_http_failures", 0)
        self.workers = []

    def event(self, name, **details):
        # Fixed event/reason identifiers only; never log HTTP bodies or credentials.
        logging.info(name, extra={"details": {"run_id": self.run_id, "state": self.session.state, **details}})

    def record_http_error(self, error):
        category = ("validation" if str(error) == "invalid_payload" else "network" if str(error) in ("network_error", "response_decode_error")
                    else "auth" if isinstance(error, (InvalidCredentials, Forbidden, LoggedOut)) or str(error) == "login_not_confirmed"
                    else "rate_limit" if str(error) == "rate_limited" else "server" if str(error) == "server_error" else "other")
        self.error_totals[category] = self.error_totals.get(category, 0) + 1
        self.store.set("error_totals", self.error_totals)
        if self.outage_tick is None:
            self.outage_tick = time.monotonic()
            self.store.set("outage_active", True)
        if str(error) == "invalid_payload":
            self.validation_failures += 1
            self.store.set("validation_diagnostics", error.details)
        else:
            self.validation_failures = 0
        self.store.set("consecutive_validation_failures", self.validation_failures)
        self.last_http_error = {"time": iso(utcnow()), "reason": str(error), **error.details}
        self.store.set("last_http_error", self.last_http_error)

    def controls(self):
        commands = self.store.apply_commands()
        if any(action in ("pause", "resume") for action, _ in commands):
            self.telemetry.invalidate()
            self.event("control_changed", action=commands[-1][0])
        until = self.store.get("paused_until", 0)
        paused = until is None or until > utcnow()
        if paused:
            self.session.state = "PAUSED"
            self.telemetry.invalidate()
        if any(action == "retry" for action, _ in commands):
            allowed = not paused and not self.session.failed and not (self.session.waiting() and self.session.protected)
            if allowed:
                self.session.due = self.session.due_mono = self.next_poll = 0
                self.store.set("next_attempt", 0)
            self.event("retry_requested", allowed=allowed)
        return paused

    def accept(self, result):
        raw, received = result
        if self.last_http_error:
            self.counters["recoveries"] = self.counters.get("recoveries", 0) + 1
            self.event("read_recovered", previous_reason=self.last_http_error["reason"],
                       gap_since_last_read_s=received - self.last_read if self.last_read else None)
        if self.outage_tick is not None:
            self.outage_elapsed += max(0, time.monotonic() - self.outage_tick)
            self.outage_tick = None
            self.store.set("outage_elapsed", self.outage_elapsed)
            self.store.set("outage_active", False)
        self.verifying = False
        self.failure_counts = {}
        self.validation_failures = 0
        self.session.failures = 0
        self.store.set("auth_failures", 0)
        self.store.set("failure_counts", {})
        self.store.set("last_valid_read", received)
        diagnostics = getattr(raw, "diagnostics", None)
        if diagnostics is not None:
            diagnostics = dict(self.http.validation_diagnostics or {}, **diagnostics)
            self.store.set("validation_diagnostics", diagnostics)
            if diagnostics["rejected_signals"]:
                self.counters["rejected_signals"] = self.counters.get("rejected_signals", 0) + diagnostics["rejected_signals"]
                self.event("partial_payload", **diagnostics)
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
        self.store.set("consecutive_http_failures", 0)
        self.store.set("consecutive_validation_failures", 0)
        self.counters["reads"] += 1
        previous = set(self.store.get("unknown_signals", []))
        unknown = set(sample["unknown_signals"])
        if unknown != previous:
            self.store.set("unknown_signals", sorted(unknown))
            self.event("new_unmapped_signals", count=len(unknown))

    def status(self):
        now = utcnow()
        if self.outage_tick is not None:
            self.outage_elapsed += max(0, time.monotonic() - self.outage_tick)
            self.outage_tick = time.monotonic()
            self.store.set("outage_elapsed", self.outage_elapsed)
        workers_alive = all(worker.is_alive() for worker in self.workers)
        self.metrics.update(self.session.state, self.counters, self.mqtt.status, workers_alive, self.diagnostics())
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
            **self.diagnostics(),
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

    def diagnostics(self):
        live = self.telemetry.snapshot()
        return {"consecutive_http_failures": self.read_failures,
                "consecutive_validation_failures": self.validation_failures,
                "error_totals": dict(self.error_totals), "outage_seconds": self.outage_elapsed,
                "last_valid_read": self.last_read,
                "seconds_since_last_read": max(0, utcnow() - self.last_read) if self.last_read is not None else None,
                "reception_health": "fresh" if live["available"] else "stale" if self.last_read is not None else "unavailable",
                "retry_remaining_s": max(0, self.session.due_mono - time.monotonic()),
                "retry_at": self.session.due, "retry_reason": self.session.reason}

    def transient_retry(self, error):
        reason = str(error)
        cfg = self.cfg["retry"]
        count = self.failure_counts.get(reason, 0) + 1
        self.failure_counts[reason] = count
        self.store.set("failure_counts", self.failure_counts)
        initial = cfg["validation_initial_s"] if reason == "invalid_payload" else cfg["transient_initial_s"]
        delay = min(cfg["max_s"], initial * 2 ** min(count - 1, 16) * random.uniform(1, 1 + cfg["jitter"]))
        self.session.wait("RETRY_BACKOFF", max(delay, error.retry_after), utcnow(), reason,
                          protected=error.retry_after > 0 or reason == "rate_limited")

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
        login = not s.authenticated and not self.verifying
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
                if self.verifying:
                    s.success(result[1])
                    self.event("login_verified")
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
            self.verifying = False
            self.http.login_submitted = False
            self.http.client.cookies.clear()
            self.telemetry.invalidate()
            if s.authenticated:
                self.event("logout", **s.loss(utcnow()), **error.details)
            else:
                s.auth_retry(utcnow(), error.retry_after)
                self.event("authentication_rejected", next_attempt=iso(s.due), **error.details)
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
            self.read_failures += 1
            self.store.set("consecutive_http_failures", self.read_failures)
            if login and self.http.login_submitted:
                # Retry verification with existing cookies; a malformed JSON body is not a failed password.
                self.verifying = True
            if login and str(error) in ("login_not_confirmed", "login_form_not_found", "unsafe_redirect", "too_many_redirects"):
                s.auth_retry(utcnow(), error.retry_after)
            else:
                self.transient_retry(error)
            self.event("http_error", reason=str(error), operation="login" if login else "read",
                       consecutive_read_failures=self.read_failures, next_attempt=iso(s.due) if s.due else None,
                       retry_s=max(0, s.due - utcnow()), **error.details)
        finally:
            # No overlapping requests or catch-up bursts, including after errors or slow responses.
            self.next_poll = time.monotonic() + (0 if s.waiting() else self.cfg["thematronic"]["poll_s"])

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
                self.metrics.update(self.session.state, self.counters, self.mqtt.status, diagnostics=self.diagnostics())
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

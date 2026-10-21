from __future__ import annotations

import random
import time


class Session:
    def __init__(self, cfg, store, now):
        self.cfg, self.store = cfg, store
        self.authenticated = False
        self.login_at = self.last_success = None
        self.failures = 0
        self.forbidden_count = 0
        self.state = "START"
        self.due = store.get("next_attempt", 0)
        # Restore UTC deadlines once; subsequent waits use the monotonic clock.
        saved_at = store.get("saved_at", now)
        if now < saved_at - 60:
            self.due = now + max(self.due - saved_at, cfg["cooldown_s"])
        self.due_mono = time.monotonic() + max(0, self.due - now)
        self.reason = store.get("wait_reason", "startup")
        self.failed = False

    def wait(self, state, seconds, now, reason):
        self.state, self.due, self.reason = state, now + seconds, reason
        self.due_mono = time.monotonic() + seconds
        self.store.set("next_attempt", self.due)
        self.store.set("saved_at", now)
        self.store.set("wait_reason", reason)

    def waiting(self):
        return time.monotonic() < self.due_mono

    def success(self, now):
        self.authenticated = True
        self.login_at = self.last_success = now
        self.failures = self.forbidden_count = 0
        self.state, self.due, self.reason = "POLLING", 0, "verified_login"
        self.due_mono = 0
        self.store.set("next_attempt", 0)

    def loss(self, now):
        lower = max(0, (self.last_success or now) - (self.login_at or now))
        upper = max(0, now - (self.login_at or now))
        expected = self.cfg["expected_session_lifetime_s"]
        tolerance = self.cfg["tolerance_s"]
        # Require the whole observed loss interval to fit. A long outage is not expiration evidence.
        expected_loss = expected is not None and lower >= expected - tolerance and upper <= expected + tolerance
        early = upper <= self.cfg["early_loss_s"]
        delay = self.cfg["quick_relog_s"] if expected_loss and not early else self.cfg["cooldown_s"]
        self.authenticated = False
        reason = "expected_expiry" if expected_loss and not early else "early_or_unclassified_loss"
        self.wait("AUTH_COOLDOWN", delay, now, reason)
        return {"lifetime_min_s": lower, "lifetime_max_s": upper, "classification": reason, "delay_s": delay}

    def auth_retry(self, now, retry_after=0):
        delay = min(300 * 2 ** min(self.failures, 4), 3600)
        self.failures += 1
        self.wait("RETRY_BACKOFF", max(min(3600, delay * random.uniform(1, 1.05)), retry_after), now, "login_retry")

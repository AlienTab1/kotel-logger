from __future__ import annotations

import threading
import time
from datetime import datetime


class Telemetry:
    """A shared live sample for MQTT and Prometheus; freshness uses monotonic time."""
    def __init__(self, stale_s):
        self.stale_s = stale_s
        self.lock = threading.Lock()
        self.latest = None
        self.received_mono = None
        self.valid = False

    def offer(self, sample, now=None):
        with self.lock:
            self.latest = sample
            self.received_mono = time.monotonic() if now is None else now
            self.valid = True

    def invalidate(self):
        with self.lock:
            self.valid = False

    def snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            age = None if self.received_mono is None else now - self.received_mono
            available = self.valid and age is not None and 0 <= age < self.stale_s
            received = datetime.fromisoformat(self.latest["received_at"]).timestamp() if self.latest else None
            return {"sample": self.latest if available else None, "age_s": age,
                    "received_at": received, "available": bool(available)}

    def current(self, now=None):
        return self.snapshot(now)["sample"]


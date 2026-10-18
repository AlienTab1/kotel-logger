"""Bounded asynchronous logging. No Loki network I/O on the collector thread."""
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading
import time

import httpx


class EventLog(logging.Handler):
    def __init__(self, cfg, formatter, transport=None):
        super().__init__()
        options = cfg["logging"]
        directory = Path(cfg["data_dir"]) / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self.file = RotatingFileHandler(directory / "events.jsonl", maxBytes=options["file_max_bytes"],
                                        backupCount=options["file_backups"], encoding="utf-8")
        self.file.setFormatter(formatter)
        self.setFormatter(formatter)
        self.url = options["loki_url"]
        self.labels = {"app": "kotel-logger", "device_id": str(cfg["thematronic"]["device_id"])}
        self.queue = queue.Queue(maxsize=options["queue_size"])
        self.dropped = self.failures = 0
        self.state = "starting" if self.url else "disabled"
        self.stop = threading.Event()
        self.transport = transport
        self.thread = threading.Thread(target=self.run, name="loki", daemon=True)
        if self.url:
            self.thread.start()

    def emit(self, record):
        # The rotating local file is independent of remote delivery.
        self.file.emit(record)
        if self.url and not getattr(record, "local_only", False):
            try:
                self.queue.put_nowait([str(int(record.created * 1_000_000_000)), self.format(record)])
            except queue.Full:
                self.dropped += 1

    def snapshot(self):
        return {"state": self.state, "queued": self.queue.qsize(), "dropped": self.dropped,
                "failures": self.failures, "worker_alive": self.thread.is_alive() if self.url else None}

    def report(self, event, **details):
        # Never feed delivery errors back into the delivery queue.
        logging.warning(event, extra={"details": details, "local_only": True})

    def run(self):
        batch = []
        delay = 1
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False, transport=self.transport) as client:
            while not self.stop.is_set():
                if not batch:
                    try:
                        batch.append(self.queue.get(timeout=0.5))
                    except queue.Empty:
                        continue
                    while len(batch) < 100:
                        try:
                            batch.append(self.queue.get_nowait())
                        except queue.Empty:
                            break
                try:
                    # Stream to avoid downloading arbitrary error response bodies.
                    with client.stream("POST", self.url, json={"streams": [{"stream": self.labels, "values": batch}]}) as response:
                        status = response.status_code
                    if status != 204:
                        self.failures += 1
                        self.state = "retrying"
                        self.report("loki_delivery_failed", http_status=status, retry_s=delay)
                        # Permanent rejection must not block every subsequent batch.
                        if 400 <= status < 500 and status not in (408, 429):
                            self.dropped += len(batch)
                            batch = []
                        self.stop.wait(delay)
                        delay = min(60, delay * 2)
                        continue
                except httpx.HTTPError as error:
                    self.failures += 1
                    self.state = "retrying"
                    self.report("loki_delivery_failed", exception_type=type(error).__name__, retry_s=delay)
                    self.stop.wait(delay)
                    delay = min(60, delay * 2)
                    continue
                if self.state != "connected":
                    self.report("loki_connected")
                self.state, batch, delay = "connected", [], 1
        self.dropped += len(batch) + self.queue.qsize()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=25)
        self.file.close()
        super().close()

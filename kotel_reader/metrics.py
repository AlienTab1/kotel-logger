from __future__ import annotations

import threading
import time

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from . import __version__


class Metrics:
    """Each scrape renders the current valid sample, with no historical replay or I/O."""
    def __init__(self, cfg, mapping, device_id, telemetry):
        self.cfg, self.mapping, self.device_id, self.telemetry = cfg, mapping, str(device_id), telemetry
        self.lock = threading.Lock()
        self.diagnostics = {"state": "START", "counters": {}, "mqtt": "disabled"}
        self.heartbeat = None
        self.registry = CollectorRegistry()
        self.registry.register(self)
        self.server = self.thread = None

    def update(self, state, counters, mqtt, workers_alive=True):
        with self.lock:
            self.diagnostics = {"state": state, "counters": dict(counters), "mqtt": mqtt, "workers_alive": workers_alive}
            self.heartbeat = time.monotonic()

    def describe(self):
        return []

    def collect(self):
        now = time.monotonic()
        with self.lock:
            diag, heartbeat = self.diagnostics, self.heartbeat
        live = self.telemetry.snapshot(now)
        healthy = heartbeat is not None and 0 <= now - heartbeat < 120 and diag.get("workers_alive", False) and diag["state"] != "STOPPED"
        available = healthy and live["available"]

        def gauge(name, help_text, value):
            metric = GaugeMetricFamily(name, help_text, labels=["device_id"])
            metric.add_metric([self.device_id], value)
            return metric

        yield gauge("kotel_collector_healthy", "Scheduler and worker health, independent of web availability.", int(healthy))
        yield gauge("kotel_telemetry_available", "A valid recently received web response is available; source freshness is unknown.", int(available))
        yield gauge("kotel_mqtt_connected", "Whether the publisher is connected to MQTT.", int(diag["mqtt"] == "connected"))
        if live["received_at"] is not None:
            yield gauge("kotel_last_received_timestamp_seconds", "UTC reception time of the last valid web response, not measurement time.", live["received_at"])
            yield gauge("kotel_receive_age_seconds", "Monotonic age of the last valid web response.", max(0, live["age_s"]))
        build = GaugeMetricFamily("kotel_build_info", "Collector version.", labels=["device_id", "version"])
        build.add_metric([self.device_id, __version__], 1)
        yield build
        state = GaugeMetricFamily("kotel_session_state", "Current collector state (one-hot).", labels=["device_id", "state"])
        for name in ("START", "AUTHENTICATING", "POLLING", "AUTH_COOLDOWN", "RETRY_BACKOFF", "PAUSED", "AUTH_FAILED", "STOPPED"):
            state.add_metric([self.device_id, name], int(diag["state"] == name))
        yield state
        for key, name, help_text in (
            ("reads", "kotel_reads", "Valid web responses accepted."),
            ("logins", "kotel_login_attempts", "Login attempts."),
            ("errors", "kotel_http_errors", "HTTP, payload and authentication errors."),
            ("losses", "kotel_session_losses", "Confirmed session losses."),
        ):
            counter = CounterMetricFamily(name, help_text, labels=["device_id"])
            counter.add_metric([self.device_id], diag["counters"].get(key, 0))
            yield counter

        families = {name: GaugeMetricFamily(name, help_text, labels=["device_id", "signal"]) for name, help_text in (
            ("kotel_temperature_celsius", "Current mapped temperature in degrees Celsius."),
            ("kotel_maximum_temperature_celsius", "Maximum temperature over an unknown period, not a current temperature."),
            ("kotel_signal_value", "Mapped numeric value; see signal configuration for meaning and units."),
            ("kotel_signal_state", "Mapped boolean as 0 or 1; physical meaning and polarity require verification."),
            ("kotel_duration_seconds", "Mapped duration in seconds, not a timestamp or counter."),
            ("kotel_signal_available", "Mapped numeric or boolean signal is valid and telemetry is available."),
        )}
        sample = live["sample"] if available else None
        for signal, meta in self.mapping.items():
            if not meta.get("prometheus", False) or meta["type"] == "string":
                continue
            value = sample["values"].get(signal) if sample else None
            valid = sample is not None and sample["statuses"].get(signal, {}).get("valid", False) and value is not None
            labels = [self.device_id, signal]
            families["kotel_signal_available"].add_metric(labels, int(valid))
            if not valid:
                continue  # Omit stale/invalid series entirely; never substitute zero or last value.
            if meta["type"] == "boolean":
                family, value = "kotel_signal_state", int(value)
            elif meta["type"] == "duration":
                family = "kotel_duration_seconds"
            elif meta.get("unit_of_measurement") == "°C":
                family = "kotel_maximum_temperature_celsius" if signal.startswith("max_t_") else "kotel_temperature_celsius"
            else:
                family = "kotel_signal_value"
            families[family].add_metric(labels, value)
        yield from families.values()

    def start(self):
        self.server, self.thread = start_http_server(self.cfg["port"], addr=self.cfg["host"], registry=self.registry)

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)

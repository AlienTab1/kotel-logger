"""Deterministic metrics target for the isolated container smoke test."""
import signal
import threading
import time

from kotel_reader.metrics import Metrics
from kotel_reader.model import normalize
from kotel_reader.telemetry import Telemetry

stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
mapping = {"s_t_spal": {"type": "number", "unit_of_measurement": "°C", "prometheus": True}}
raw = [{"Nazev": "s_t_spal", "Hodnota": 222.1, "Stav": "ok", "HodnotaF": "222,1 °C"}]
telemetry = Telemetry(60)
metrics = Metrics({"host": "0.0.0.0", "port": 9108}, mapping, 2985, telemetry)
metrics.start()
try:
    while not stop.is_set():
        telemetry.offer(normalize(raw, 2985, mapping, time.time()))
        metrics.update("POLLING", {"reads": 1}, "disabled")
        stop.wait(0.2)
finally:
    metrics.close()


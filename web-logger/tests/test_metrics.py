import threading

import httpx
import pytest
from prometheus_client import generate_latest
from prometheus_client.parser import text_string_to_metric_families

from kotel_reader.metrics import Metrics
from kotel_reader.model import normalize
from kotel_reader.telemetry import Telemetry


def samples(metrics):
    return [s for family in text_string_to_metric_families(generate_latest(metrics.registry).decode()) for s in family.samples]


def values(metrics, name):
    return [s.value for s in samples(metrics) if s.name == name]


@pytest.fixture
def exporter(cfg, clock):
    telemetry = Telemetry(cfg["telemetry"]["stale_s"])
    metric = Metrics(cfg["prometheus"], cfg["signals"], 2985, telemetry)
    metric.update("POLLING", {"reads": 3}, "disabled")
    return metric


def test_no_data_at_start(exporter):
    assert values(exporter, "kotel_collector_healthy") == [1]
    assert values(exporter, "kotel_telemetry_available") == [0]
    assert values(exporter, "kotel_temperature_celsius") == []
    assert values(exporter, "kotel_last_received_timestamp_seconds") == []
    assert values(exporter, "kotel_signal_available") == [0]


def test_live_sample_expiry_and_recovery(exporter, cfg, raw, clock):
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    assert values(exporter, "kotel_temperature_celsius") == [222.1]
    assert values(exporter, "kotel_telemetry_available") == [1]
    assert values(exporter, "kotel_reads_total") == [3]
    for s in samples(exporter):
        assert s.timestamp is None  # Prometheus timestamps scrapes; never replay source times.
        assert "sample_id" not in s.labels
    clock.advance(59.9)
    assert values(exporter, "kotel_temperature_celsius") == [222.1]
    clock.advance(0.1)
    assert values(exporter, "kotel_temperature_celsius") == []
    assert values(exporter, "kotel_signal_available") == [0]
    assert values(exporter, "kotel_last_received_timestamp_seconds") == [10000]
    assert values(exporter, "kotel_receive_age_seconds") == [60]
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    assert values(exporter, "kotel_temperature_celsius") == [222.1]


def test_invalid_missing_and_unmapped_values(exporter, cfg, raw, clock):
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    raw[0].update(Hodnota=-50.1, Stav="nepripojeno", HodnotaF="---")
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    assert values(exporter, "kotel_temperature_celsius") == []
    assert values(exporter, "kotel_signal_available") == [0]
    unknown = [{"Nazev": "unknown_secret_text", "Hodnota": "arbitrary text", "Stav": "ok", "HodnotaF": "value"}]
    exporter.telemetry.offer(normalize(unknown, 2985, cfg["signals"], clock.time()))
    assert values(exporter, "kotel_temperature_celsius") == []
    text = generate_latest(exporter.registry).decode()
    assert "unknown_secret_text" not in text and "arbitrary text" not in text


def test_types_maxima_and_independent_publication(cfg, clock):
    mapping = {
        "bool": {"type": "boolean", "prometheus": True, "publish": False},
        "duration": {"type": "duration", "prometheus": True},
        "number": {"type": "number", "prometheus": True},
        "max_t_spal": {"type": "number", "unit_of_measurement": "°C", "prometheus": True},
        "mqtt_only": {"type": "number", "prometheus": False, "publish": True},
        "text": {"type": "string", "prometheus": True},
    }
    raw = [{"Nazev": n, "Hodnota": v, "Stav": "nezname", "HodnotaF": str(v)} for n, v in
           (("bool", True), ("duration", "PT1H28M"), ("number", 521), ("max_t_spal", 250), ("mqtt_only", 10), ("text", "operating"))]
    telemetry = Telemetry(60)
    exporter = Metrics(cfg["prometheus"], mapping, 1, telemetry)
    exporter.update("POLLING", {}, "retrying")
    telemetry.offer(normalize(raw, 1, mapping, clock.time()))
    assert values(exporter, "kotel_signal_state") == [1]
    assert values(exporter, "kotel_duration_seconds") == [5280]
    assert values(exporter, "kotel_signal_value") == [521]
    assert values(exporter, "kotel_maximum_temperature_celsius") == [250]
    assert values(exporter, "kotel_temperature_celsius") == []
    assert len(values(exporter, "kotel_signal_available")) == 4
    assert values(exporter, "kotel_mqtt_connected") == [0]


def test_pause_invalidates_until_new_sample(exporter, cfg, raw, clock):
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    exporter.telemetry.invalidate()
    exporter.update("PAUSED", {}, "connected")
    assert values(exporter, "kotel_temperature_celsius") == []
    assert values(exporter, "kotel_collector_healthy") == [1]
    exporter.update("POLLING", {}, "connected")
    assert values(exporter, "kotel_temperature_celsius") == []


def test_scheduler_stall_hides_values_even_with_long_stale_limit(exporter, cfg, raw, clock):
    exporter.telemetry.stale_s = 1000
    exporter.telemetry.offer(normalize(raw, 2985, cfg["signals"], clock.time()))
    clock.advance(120)
    assert values(exporter, "kotel_collector_healthy") == [0]
    assert values(exporter, "kotel_temperature_celsius") == []


@pytest.mark.loopback
def test_real_http_scrape_and_shutdown(cfg, raw):
    telemetry = Telemetry(60)
    exporter = Metrics({"host": "127.0.0.1", "port": 0}, cfg["signals"], 2985, telemetry)
    exporter.update("POLLING", {}, "disabled")
    telemetry.offer(normalize(raw, 2985, cfg["signals"], 10000))
    exporter.start()
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(f"http://127.0.0.1:{exporter.server.server_port}/metrics")
            assert response.status_code == 200
            assert 'kotel_temperature_celsius{device_id="2985",signal="s_t_spal"} 222.1' in response.text
            telemetry.invalidate()
            response = client.get(f"http://127.0.0.1:{exporter.server.server_port}/metrics")
            parsed = [s for family in text_string_to_metric_families(response.text) for s in family.samples]
            assert not any(s.name == "kotel_temperature_celsius" for s in parsed)
    finally:
        exporter.close()
    assert not exporter.thread.is_alive()


@pytest.mark.loopback
def test_scrape_does_not_wait_for_a_slow_thematronic_request(cfg, raw, monkeypatch):
    from kotel_reader.service import Service
    from kotel_reader.model import utcnow
    monkeypatch.setenv("THEMATRONIC_USERNAME", "test")
    monkeypatch.setenv("THEMATRONIC_PASSWORD", "test")
    cfg["prometheus"] = {"host": "127.0.0.1", "port": 0}
    service = Service(cfg)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def slow_login():
        entered.set()
        release.wait(10)
        return raw, utcnow()
    monkeypatch.setattr(service.http, "login", slow_login)
    def run():
        try:
            service.run()
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(f"http://127.0.0.1:{service.metrics.server.server_port}/metrics")
        assert response.status_code == 200
        assert 'kotel_collector_healthy{device_id="2985"} 1.0' in response.text
        assert 'kotel_telemetry_available{device_id="2985"} 0.0' in response.text
        assert not release.is_set()
    finally:
        service.stop.set()
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert not errors

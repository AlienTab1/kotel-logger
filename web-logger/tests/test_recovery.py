import json

import httpx
import pytest
from prometheus_client import generate_latest

from kotel_reader.http import Thematronic, RemoteError
from kotel_reader.model import recover_payload, normalize, PayloadError
from kotel_reader.session import Session
from .test_service import service
from .test_http import FORM


def item(name, value=12):
    return dict(Nazev=name, Hodnota=value, Stav="ok", HodnotaF=str(value))


@pytest.mark.parametrize("count", [12, 18, 130])
def test_partial_arrays_and_order(cfg, count):
    raw = [item("s_t_spal")] + [item(f"optional_{i}") for i in range(count - 1)]
    client = Thematronic(cfg["thematronic"], "u", "p", httpx.MockTransport(lambda r: httpx.Response(200, json=raw)), cfg["signals"])
    try:
        result, _ = client.read()
    finally:
        client.close()
    assert result == raw
    assert result.diagnostics["accepted_signals"] == count


@pytest.mark.parametrize("name,category", [("", "name_empty"), ("  ", "name_empty"), (None, "name_wrong_type"),
    ("SECRET/token", "name_malformed")])
def test_bad_optional_identifier_does_not_destroy_measurements(cfg, name, category):
    raw = [item("s_t_spal"), item(name)]
    result = recover_payload(raw, cfg["signals"])
    assert len(result) == 1
    assert result.diagnostics["rejected_signals"] == 1
    assert result.diagnostics["issues"][0]["category"] == category
    assert "SECRET" not in json.dumps(result.diagnostics)


@pytest.mark.parametrize("value", [12, 99])
def test_duplicates_never_select_a_winner(cfg, value):
    cfg["signals"]["other"] = {"type": "number"}
    result = recover_payload([item("s_t_spal"), item("other"), item("s_t_spal", value)], cfg["signals"])
    assert [r["Nazev"] for r in result] == ["other"]
    assert result.diagnostics["issues"][0]["conflicting_indices"] == [0, 2]
    sample = normalize(result, 2985, cfg["signals"], 0)
    assert sample["values"]["s_t_spal"] is None


@pytest.mark.parametrize("bad", [[], [item("s_t_spal", "wrong")], [item("s_t_spal"), item("s_t_spal", 99)]])
def test_required_signals_fail_closed(cfg, bad):
    cfg["signals"]["s_t_spal"]["required"] = True
    with pytest.raises(PayloadError, match="required_signal_unavailable"):
        recover_payload(bad + [item("other")], cfg["signals"])


def test_missing_optional_is_unavailable(cfg):
    cfg["signals"]["optional"] = {"type": "number"}
    result = recover_payload([item("s_t_spal")], cfg["signals"])
    assert normalize(result, 1, cfg["signals"], 0)["values"]["optional"] is None


@pytest.mark.parametrize("kind", ["server", "network"])
def test_http_failure_then_recovery_without_relogin(service, clock, kind):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            if kind == "network":
                raise httpx.ConnectTimeout("secret")
            return httpx.Response(503)
        return httpx.Response(200, json=[item("s_t_spal")])
    service.http.close()
    service.http = Thematronic(service.cfg["thematronic"], "u", "p", httpx.MockTransport(handler), service.cfg["signals"])
    service.session.success(clock.time())
    service.cycle()
    assert service.session.authenticated
    assert 10 <= service.session.due - clock.time() <= 11
    clock.advance(11)
    service.cycle()
    assert service.telemetry.current() is not None
    assert service.counters["recoveries"] == 1
    assert calls == ["/api/json/reply/DeviceSignal"] * 2


def test_invalid_login_verification_retries_data_not_password(service, clock):
    calls = []
    reads = 0
    def handler(request):
        nonlocal reads
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, text=FORM)
        if request.url.path == "/sign/in/":
            return httpx.Response(200, text="ok")
        reads += 1
        return httpx.Response(200, json=[item("")] if reads < 10 else [item("s_t_spal")])
    service.http.close()
    service.http = Thematronic(service.cfg["thematronic"], "u", "p", httpx.MockTransport(handler), service.cfg["signals"])
    for _ in range(9):
        service.cycle()
        assert 15 <= service.session.due - clock.time() <= 60
        assert service.verifying and not service.session.authenticated
        clock.advance(60)
    service.cycle()
    assert service.session.authenticated
    assert service.counters["logins"] == 1
    assert calls.count(("POST", "/sign/in/")) == 1
    assert service.validation_failures == 0


def test_401_then_reauthentication(service, clock):
    reads = 0
    def handler(request):
        nonlocal reads
        if request.method == "GET":
            return httpx.Response(200, text=FORM)
        if request.url.path == "/sign/in/":
            return httpx.Response(200, text="ok")
        reads += 1
        return httpx.Response(401) if reads == 1 else httpx.Response(200, json=[item("s_t_spal")])
    service.http.close()
    service.http = Thematronic(service.cfg["thematronic"], "u", "p", httpx.MockTransport(handler), service.cfg["signals"])
    service.session.success(clock.time())
    service.cycle()
    assert service.session.due - clock.time() == 60
    clock.advance(60)
    service.cycle()
    assert service.session.authenticated and service.telemetry.current()


def test_live_but_stale_during_prolonged_outage(service, clock, monkeypatch, raw):
    service.accept((raw, clock.time()))
    service.session.success(clock.time())
    monkeypatch.setattr(service.http, "read", lambda: (_ for _ in ()).throw(RemoteError("server_error")))
    for _ in range(70):
        service.cycle()
        clock.advance(60)
        service.status()
    state = service.store.get("status")
    assert state["workers_alive"] and not state["telemetry_available"]
    assert state["reception_health"] == "stale"
    text = generate_latest(service.metrics.registry).decode()
    assert 'kotel_collector_healthy{device_id="2985"} 1.0' in text
    assert 'kotel_temperature_celsius{device_id=' not in text
    assert service.outage_elapsed >= 3600
    monkeypatch.setattr(service.http, "read", lambda: (raw, clock.time()))
    service.cycle()
    assert service.telemetry.current() and service.read_failures == 0


@pytest.mark.parametrize("protected,paused,allowed", [(False, False, True), (True, False, False), (False, True, False)])
def test_manual_retry_respects_protection(service, clock, protected, paused, allowed):
    service.session.wait("RETRY_BACKOFF", 600, clock.time(), "server_error", protected=protected)
    if paused:
        service.store.command("pause", clock.time() + 100)
    service.store.command("retry")
    service.controls()
    assert (not service.session.waiting()) == allowed


def test_restart_clock_rollback_does_not_multiply_cooldown(cfg, store, clock):
    session = Session(cfg["session"], store, clock.time())
    session.wait("RETRY_BACKOFF", 600, clock.time(), "rate_limited", protected=True)
    restarted = Session(cfg["session"], store, clock.time() - 100000)
    assert restarted.due - (clock.time() - 100000) == 600
    assert restarted.protected


def test_legacy_transient_deadline_is_bounded(cfg, store, clock):
    store.set("next_attempt", clock.time() + 3600)
    store.set("saved_at", clock.time())
    store.set("wait_reason", "invalid_payload")
    restarted = Session(cfg["session"], store, clock.time())
    assert restarted.due - clock.time() == 60


def test_legacy_login_validation_wait_is_not_auth_throttle(cfg, store, clock):
    store.set("next_attempt", clock.time() + 3600)
    store.set("saved_at", clock.time())
    store.set("wait_reason", "login_retry")
    store.set("last_http_error", {"reason": "invalid_payload"})
    restarted = Session(cfg["session"], store, clock.time())
    assert restarted.due - clock.time() == 60
    assert not restarted.protected


def test_partial_payload_ha_availability_and_prometheus(service, clock):
    from jinja2 import Environment
    from kotel_reader.mqtt import discovery
    service.cfg["signals"]["optional"] = {"type": "number", "publish": True, "prometheus": True}
    accepted = recover_payload([item("s_t_spal"), item("optional"), item("optional", 2)], service.cfg["signals"])
    service.accept((accepted, clock.time()))
    sample = service.telemetry.current()
    entities = dict(discovery(service.cfg["mqtt"], service.cfg["signals"], 2985, 60))
    optional = next(value for key, value in entities.items() if key.endswith("_optional/config"))
    assert Environment().from_string(optional["availability"][-1]["value_template"]).render(value_json=sample) == "offline"
    service.status()
    metrics = generate_latest(service.metrics.registry).decode()
    assert 'signal="optional"} 0.0' in metrics
    assert 'kotel_temperature_celsius{device_id="2985",signal="s_t_spal"} 12.0' in metrics


def test_explicit_legacy_config_preserved(tmp_path):
    from kotel_reader.config import load
    (tmp_path / "signals.yaml").write_text("s_t_spal: {type: number, required: true}")
    path = tmp_path / "config.yaml"
    path.write_text("mqtt: {enabled: false}\nthematronic: {timeout_s: 25}\nsession: {cooldown_s: 300}\n")
    loaded = load(path)
    assert loaded["thematronic"]["connect_timeout_s"] == 25
    assert loaded["thematronic"]["read_timeout_s"] == 25
    assert loaded["session"]["cooldown_s"] == 300

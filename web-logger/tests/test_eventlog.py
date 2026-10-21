import json
import logging
import threading

import httpx
import pytest

from kotel_reader.eventlog import EventLog
from kotel_reader.http import RemoteError, Thematronic
from kotel_reader.service import JsonLog


@pytest.mark.parametrize("payload,reason", [([], "empty_array"), ({"token": "SECRET"}, "expected_array"),
    ([{"Nazev": "secret", "Hodnota": {}, "Stav": "ok", "HodnotaF": "SECRET"}], "invalid_value_type")])
def test_payload_diagnostics_are_specific_and_private(cfg, payload, reason):
    client = Thematronic(cfg["thematronic"], "user", "password",
                         httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    try:
        with pytest.raises(RemoteError) as caught:
            client.read()
        details = caught.value.details
        assert details["validation_reason"] == reason
        assert details["response_bytes"] > 0
        assert len(details["body_sha256"]) == 64
        assert "SECRET" not in json.dumps(details)
        if isinstance(payload, list) and payload:
            assert details["item_index"] == 0
            assert details["field_types"]["Hodnota"] == "dict"
    finally:
        client.close()


def record():
    item = logging.LogRecord("root", logging.INFO, "", 0, "test_event", (), None)
    item.details = {"reason": "test"}
    return item


def test_local_rotation_without_loki(cfg, tmp_path):
    cfg["logging"].update(file_max_bytes=200, file_backups=2)
    sink = EventLog(cfg, JsonLog())
    try:
        for _ in range(20):
            sink.emit(record())
        assert sink.snapshot()["state"] == "disabled"
    finally:
        sink.close()
    files = list((tmp_path / "logs").glob("events.jsonl*"))
    assert len(files) == 3
    assert json.loads((tmp_path / "logs/events.jsonl").read_text())["event"] == "test_event"


def test_loki_queue_does_not_block_and_sends_valid_payload(cfg):
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        entered.set()
        release.wait(3)
        delivered.set()
        return httpx.Response(204)
    cfg["logging"].update(loki_url="http://loki/loki/api/v1/push", queue_size=1)
    sink = EventLog(cfg, JsonLog(), httpx.MockTransport(handler))
    try:
        sink.emit(record())
        assert entered.wait(2)
        for _ in range(10):
            sink.emit(record())
        assert sink.dropped >= 9
        release.set()
        assert delivered.wait(2)
    finally:
        release.set()
        sink.close()
    stream = requests[0]["streams"][0]
    assert stream["stream"] == {"app": "kotel-logger", "device_id": "2985"}
    assert stream["values"][0][0].isdigit()
    assert json.loads(stream["values"][0][1])["event"] == "test_event"


def test_loki_failure_preserves_local_log(cfg, tmp_path):
    called = threading.Event()
    def handler(request):
        called.set()
        raise httpx.ConnectTimeout("URL with SECRET")
    cfg["logging"]["loki_url"] = "http://loki/loki/api/v1/push"
    sink = EventLog(cfg, JsonLog(), httpx.MockTransport(handler))
    try:
        sink.emit(record())
        assert called.wait(2)
    finally:
        sink.close()
    assert sink.failures == 1
    assert "test_event" in (tmp_path / "logs/events.jsonl").read_text()


def test_503_retry_after_and_safe_html_fingerprint(cfg):
    client = Thematronic(cfg["thematronic"], "user", "password", httpx.MockTransport(
        lambda r: httpx.Response(503, text="<h1>Service Unavailable</h1>nginx token=SECRET", headers={"Retry-After": "600", "Server": "nginx SECRET"})))
    try:
        with pytest.raises(RemoteError) as caught:
            client.read()
        assert caught.value.retry_after == 600
        details = caught.value.details
        assert details["retry_after_s"] == 600
        assert details["server_family"] == "nginx"
        assert details["error_page_markers"] == ["service unavailable", "nginx"]
        assert "SECRET" not in json.dumps(details)
    finally:
        client.close()


def test_old_config_and_loki_env_override(tmp_path, monkeypatch):
    from kotel_reader.config import load
    (tmp_path / "config.yaml").write_text("mqtt:\n  enabled: false\n")
    (tmp_path / "signals.yaml").write_text("{}")
    monkeypatch.delenv("KOTEL_LOKI_URL", raising=False)
    assert load(tmp_path / "config.yaml")["logging"]["loki_url"] == ""
    monkeypatch.setenv("KOTEL_LOKI_URL", "http://loki.skritek.jankovice/loki/api/v1/push")
    assert load(tmp_path / "config.yaml")["logging"]["loki_url"].startswith("http://loki.")
    monkeypatch.setenv("KOTEL_LOKI_URL", "http://user:SECRET@loki/push")
    with pytest.raises(ValueError, match="without credentials"):
        load(tmp_path / "config.yaml")

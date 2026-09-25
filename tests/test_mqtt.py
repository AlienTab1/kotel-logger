import json
import threading
from types import SimpleNamespace

from jinja2 import Environment, StrictUndefined

from kotel_reader.model import normalize
from kotel_reader.mqtt import Publisher, discovery
from kotel_reader.telemetry import Telemetry


class FakeClient:
    def __init__(self):
        self.messages = []

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, payload, qos, retain))
        return SimpleNamespace(rc=0)

    def subscribe(self, *args, **kwargs):
        pass


def test_discovery_missing_and_boolean(cfg, raw):
    cfg["signals"]["b"] = {"type": "boolean", "publish": True, "component": "binary_sensor", "entity_category": "diagnostic"}
    env = Environment(undefined=StrictUndefined)
    sample = normalize(raw, 2985, cfg["signals"], 100)
    for topic, config in discovery(cfg["mqtt"], cfg["signals"], 2985, cfg["telemetry"]["stale_s"]):
        assert "command_topic" not in config
        assert config["availability_mode"] == "all"
        if "statuses" in config.get("availability", [{}])[-1].get("value_template", ""):
            template = config["availability"][-1]["value_template"]
            expected = "offline" if "/binary_sensor/" in topic else "online"
            assert env.from_string(template).render(value_json=sample) == expected
            assert env.from_string(template).render(value_json={"statuses": {}}) == "offline"
        env.from_string(config["value_template"]).render(value_json=sample)


def test_birth_reconnect_never_revives_expired_state(cfg, raw, monkeypatch):
    pub = Publisher(cfg["mqtt"], cfg["signals"], 2985, threading.Event(), Telemetry(cfg["telemetry"]["stale_s"]))
    sample = normalize(raw, 2985, cfg["signals"], 100)
    pub.telemetry.offer(sample, now=100)
    monkeypatch.setattr("kotel_reader.mqtt.time.monotonic", lambda: 101)
    client = FakeClient()
    pub.refresh = True
    assert pub._send(client, False)
    states = [m for m in client.messages if m[0].endswith("/state")]
    assert len(states) == 1 and states[0][3] is False and states[0][2] == 1
    assert "raw" not in json.loads(states[0][1])
    monkeypatch.setattr("kotel_reader.mqtt.time.monotonic", lambda: 161)
    client.messages.clear()
    pub._message(client, None, SimpleNamespace(topic="homeassistant/status", payload=b"online"))
    assert not pub._send(client, True)
    assert not any(m[0].endswith("/state") for m in client.messages)
    assert any(m[1] == "offline" for m in client.messages)


def test_pause_invalidates_even_recent_sample(cfg, raw):
    pub = Publisher(cfg["mqtt"], cfg["signals"], 2985, threading.Event(), Telemetry(cfg["telemetry"]["stale_s"]))
    pub.telemetry.offer(normalize(raw, 1, cfg["signals"], 100), now=100)
    assert pub.telemetry.current(101)
    pub.telemetry.invalidate()
    assert pub.telemetry.current(101) is None

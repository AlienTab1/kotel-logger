"""Runs only inside the disposable compose.test.yaml network; never the real boiler."""
import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import pytest

from kotel_reader.model import normalize
from kotel_reader.mqtt import Publisher
from kotel_reader.telemetry import Telemetry
import httpx
from pathlib import Path

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("KOTEL_INTEGRATION") != "1", reason="Requires disposable compose.test.yaml services")]


def test_mqtt_real_broker_birth_and_expiration(cfg, raw):
    cfg["mqtt"].update(enabled=True, host="mqtt")
    stop = threading.Event()
    pub = Publisher(cfg["mqtt"], cfg["signals"], 2985, stop, Telemetry(1))
    received = []
    ready = threading.Event()
    observer = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="integration-observer")
    def connected(client, userdata, flags, reason, props):
        client.subscribe("#", qos=1)
    observer.on_connect = connected
    observer.on_subscribe = lambda *args: ready.set()
    observer.on_message = lambda c, u, msg: received.append((msg.topic, msg.payload.decode(), msg.retain))
    deadline = time.monotonic() + 15
    while True:
        try:
            observer.connect("mqtt", 1883, 20)
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
    observer.loop_start()
    thread = threading.Thread(target=pub.run)
    thread.start()
    try:
        assert ready.wait(5)
        deadline = time.monotonic() + 8
        while not pub.connected and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pub.connected
        pub.telemetry.offer(normalize(raw, 2985, cfg["signals"], time.time()))
        deadline = time.monotonic() + 8
        while not any(t.endswith("/state") for t, _, _ in received) and time.monotonic() < deadline:
            time.sleep(0.05)
        states = [(t, p, r) for t, p, r in received if t.endswith("/state")]
        assert states and not states[-1][2]
        assert json.loads(states[-1][1])["values"]["s_t_spal"] == 222.1
        time.sleep(1.2)
        count = sum(t.endswith("/state") for t, _, _ in received)
        observer.publish("homeassistant/status", "online", qos=1)
        time.sleep(0.5)
        assert sum(t.endswith("/state") for t, _, _ in received) == count
        assert any(t.endswith("/availability") and p == "offline" for t, p, _ in received)
    finally:
        stop.set()
        thread.join(10)
        observer.disconnect()
        observer.loop_stop()


def test_real_prometheus_scrapes_exporter_and_accepts_dashboard_queries():
    with httpx.Client(base_url="http://prometheus:9090", timeout=5, trust_env=False) as client:
        deadline = time.monotonic() + 30
        query = 'kotel_temperature_celsius{job="kotel-reader",device_id="2985",signal="s_t_spal"}'
        result = []
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/v1/query", params={"query": query})
                response.raise_for_status()
                result = response.json()["data"]["result"]
                if result:
                    break
            except (httpx.HTTPError, KeyError):
                pass
            time.sleep(0.2)
        assert result and float(result[0]["value"][1]) == 222.1
        dashboard = json.loads(Path("deploy/grafana-dashboard.json").read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for target in panel["targets"]:
                expr = target["expr"].replace("$__rate_interval", "1m").replace("$job", "kotel-reader").replace("$instance", "exporter:9108").replace("$device_id", "2985")
                response = client.get("/api/v1/query", params={"query": expr})
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "success"


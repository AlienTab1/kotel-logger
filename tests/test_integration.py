"""Runs only inside the disposable compose.test.yaml network; never the real boiler."""
import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import pytest

from kotel_reader.model import normalize
from kotel_reader.mqtt import Publisher
from kotel_reader.postgres import DatabaseWorker, connect, migrate, write

pytestmark = [pytest.mark.integration, pytest.mark.skipif(os.environ.get("KOTEL_INTEGRATION") != "1", reason="Requires disposable compose.test.yaml services")]


@pytest.fixture
def db_cfg(cfg):
    cfg["postgres"].update(enabled=True, host="postgres", port=5432, dbname="kotelna_test", user="test", sslmode="disable")
    migrate(cfg["postgres"], "migrations")
    return cfg["postgres"]


def test_postgres_commit_crash_replay_idempotence(db_cfg, store, raw, cfg):
    sample = normalize(raw, 2985, cfg["signals"], 1700000000)
    store.enqueue("snapshot", sample, time.time())
    seq, kind, item = store.peek()
    with connect(db_cfg) as conn:
        write(conn, kind, item)
        conn.commit()
    # Simulated crash before local ack: the exact same UUID and timestamp are replayed.
    with connect(db_cfg) as conn:
        write(conn, kind, item)
        conn.commit()
        row = conn.execute("SELECT count(*),min(received_at) FROM kotel.snapshots WHERE sample_id=%s", (sample["sample_id"],)).fetchone()
        assert row[0] == 1
        assert row[1].timestamp() == 1700000000
        assert conn.execute("SELECT temperature FROM kotel.temperatures WHERE device_id=2985 AND signal='s_t_spal'").fetchone()[0] == 222.1
    store.ack(seq)
    assert store.peek() is None


def test_mqtt_real_broker_birth_and_expiration(cfg, raw):
    cfg["mqtt"].update(enabled=True, host="mqtt", stale_s=1)
    stop = threading.Event()
    pub = Publisher(cfg["mqtt"], cfg["signals"], 2985, stop)
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
        pub.offer(normalize(raw, 2985, cfg["signals"], time.time()))
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


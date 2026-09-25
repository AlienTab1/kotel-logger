import io
import json

import pytest
from filelock import FileLock, Timeout

from kotel_reader.session import Session
from kotel_reader.storage import Store


@pytest.mark.parametrize("expected,age,last,delay", [(None, 3600, 3585, 300), (3600, 3600, 3585, 30),
    (3600, 3660, 3645, 30), (3600, 3661, 3646, 300), (3600, 3540, 3525, 300),
    (3600, 3555, 3540, 30), (3600, 3600, 3000, 300), (100, 100, 90, 300), (3600, 200, 185, 300)])
def test_loss_window(cfg, store, expected, age, last, delay):
    cfg["session"]["expected_session_lifetime_s"] = expected
    session = Session(cfg["session"], store, 10000)
    session.success(10000)
    session.last_success = 10000 + last
    detail = session.loss(10000 + age)
    assert detail["delay_s"] == delay
    resumed = Session(cfg["session"], store, 10000 + age + 1)
    assert resumed.due == 10000 + age + delay
    assert resumed.authenticated is False


def test_login_backoff(cfg, store, monkeypatch):
    monkeypatch.setattr("kotel_reader.session.random.uniform", lambda a, b: 1)
    session = Session(cfg["session"], store, 10000)
    for delay in (300, 600, 1200, 2400, 3600, 3600):
        session.auth_retry(10000)
        assert session.due == 10000 + delay
    session.auth_retry(10000, 10000)
    assert session.due == 20000


def test_backoff_cap_and_server_retry_after_survive_restart(cfg, store, monkeypatch):
    monkeypatch.setattr("kotel_reader.session.random.uniform", lambda a, b: 1.05)
    session = Session(cfg["session"], store, 10000)
    session.failures = 10
    session.auth_retry(10000)
    assert session.due == 13600
    session.auth_retry(10000, 172800)
    restarted = Session(cfg["session"], store, 10001)
    assert restarted.due == 182800


def test_queue_durable_ack_and_export(tmp_path, cfg):
    queue = Store(tmp_path, cfg["queue"])
    queue.enqueue("snapshot", {"sample_id": "stable", "received_at": "original"}, 100)
    row = queue.peek()
    queue.close()
    queue = Store(tmp_path, cfg["queue"])
    assert queue.peek() == row
    out = io.StringIO()
    queue.export(out)
    assert json.loads(out.getvalue())["data"]["received_at"] == "original"
    queue.ack(row[0])
    assert queue.peek() is None
    queue.close()


def test_limits_and_loss_counter(store):
    store.limits["max_items"] = 2
    for i in range(3):
        store.enqueue("snapshot", {"sample_id": str(i)}, 100)
    assert store.stats()["lost_items"] == 1
    assert store.peek()[2]["sample_id"] == "1"
    assert store.prune(100 + store.limits["max_age_s"] + 1) == 2
    assert store.stats()["lost_items"] == 3
    store.limits["max_bytes"] = 5
    assert store.enqueue("snapshot", {"sample_id": "large"}, 9999999) == 1
    assert store.peek() is None


def test_pause_survives_restart(store, cfg):
    store.command("pause", 20000)
    store.apply_commands()
    assert store.get("paused_until") == 20000
    Session(cfg["session"], store, 10000)
    assert store.get("paused_until") == 20000
    store.command("resume")
    store.apply_commands()
    assert store.get("paused_until") == 0


def test_second_instance_rejected(tmp_path):
    path = str(tmp_path / "collector.lock")
    with FileLock(path, timeout=0):
        with pytest.raises(Timeout):
            FileLock(path, timeout=0).acquire()

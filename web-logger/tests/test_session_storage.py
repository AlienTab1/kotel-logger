import json
import sqlite3

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


def test_state_and_one_snapshot_survive_restart(tmp_path):
    store = Store(tmp_path)
    store.command("pause", 20000)
    store.apply_commands()
    for i in range(3):
        store.set("latest_snapshot", {"sample_id": str(i), "received_at": "original"})
    store.close()
    store = Store(tmp_path)
    assert store.get("paused_until") == 20000
    assert store.get("latest_snapshot")["sample_id"] == "2"
    assert store.db.execute("SELECT count(*) FROM state WHERE key='latest_snapshot'").fetchone()[0] == 1
    assert not store.db.execute("SELECT 1 FROM sqlite_master WHERE name='queue'").fetchone()
    store.close()


def test_upgrade_preserves_cooldown_and_leaves_legacy_queue_untouched(tmp_path):
    legacy = sqlite3.connect(tmp_path / "queue.sqlite")
    legacy.executescript("CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE queue(payload TEXT);")
    legacy.execute("INSERT INTO state VALUES ('paused_until','20000')")
    legacy.execute("INSERT INTO state VALUES ('next_attempt','21000')")
    legacy.execute("INSERT INTO queue VALUES ('historical payload')")
    legacy.commit()
    legacy.close()
    store = Store(tmp_path)
    assert store.get("paused_until") == 20000
    assert store.get("next_attempt") == 21000
    assert store.get("latest_snapshot") is None
    store.set("paused_until", 0)
    store.close()
    store = Store(tmp_path)
    assert store.get("paused_until") == 0  # import occurs only once
    store.close()
    with sqlite3.connect(tmp_path / "queue.sqlite") as legacy:
        assert legacy.execute("SELECT payload FROM queue").fetchone()[0] == "historical payload"

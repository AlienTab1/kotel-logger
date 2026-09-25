import threading
from contextlib import contextmanager

from kotel_reader.postgres import DatabaseWorker


class FastStop:
    def __init__(self):
        self.stopped = False
        self.waits = []

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.waits.append(delay)
        return self.stopped


def test_database_outage_retries_and_acks_only_after_commit(cfg, store, monkeypatch):
    cfg["postgres"]["enabled"] = True
    store.enqueue("snapshot", {"sample_id": "stable"}, 10000)
    stop = FastStop()
    calls = []
    attempts = 0

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @contextmanager
        def transaction(self):
            yield

        def execute(self, *args):
            pass

        def commit(self):
            calls.append("commit")

    def connect(_):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            assert store.stats()["items"] == 1
            raise ConnectionError("credentials should never appear in logs")
        return Connection()

    def write(conn, kind, item):
        calls.append("write")

    original_ack = store.ack
    def ack(seq):
        assert calls == ["write", "commit"]
        original_ack(seq)
        stop.stopped = True

    monkeypatch.setattr("kotel_reader.postgres.connect", connect)
    monkeypatch.setattr("kotel_reader.postgres.write", write)
    monkeypatch.setattr(store, "ack", ack)
    worker = DatabaseWorker(cfg["postgres"], store, stop)
    worker.run()
    assert attempts == 3
    assert len(stop.waits) == 2 and stop.waits[1] > stop.waits[0]
    assert store.peek() is None


def test_crash_after_commit_keeps_same_queued_sample(cfg, store, monkeypatch):
    cfg["postgres"]["enabled"] = True
    store.enqueue("snapshot", {"sample_id": "stable", "received_at": "original"}, 10000)
    stop = FastStop()
    committed = set()
    deliveries = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def commit(self):
            pass

        @contextmanager
        def transaction(self):
            yield

        def execute(self, *args):
            pass

    def write(conn, kind, item):
        committed.add(item["sample_id"])
        deliveries.append(item.copy())

    original_ack = store.ack
    def ack(seq):
        if len(deliveries) == 1:
            raise ConnectionError("simulated crash after commit before ack")
        original_ack(seq)
        stop.stopped = True

    monkeypatch.setattr("kotel_reader.postgres.connect", lambda _: Connection())
    monkeypatch.setattr("kotel_reader.postgres.write", write)
    monkeypatch.setattr(store, "ack", ack)
    DatabaseWorker(cfg["postgres"], store, stop).run()
    assert deliveries == [{"sample_id": "stable", "received_at": "original"}] * 2
    assert committed == {"stable"}


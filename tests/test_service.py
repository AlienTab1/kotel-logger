import pytest

from kotel_reader.http import Forbidden, InvalidCredentials, LoggedOut, RemoteError
from kotel_reader.service import Service


@pytest.fixture
def service(cfg, monkeypatch, clock):
    monkeypatch.setenv("THEMATRONIC_USERNAME", "test")
    monkeypatch.setenv("THEMATRONIC_PASSWORD", "test")
    service = Service(cfg)
    yield service
    service.http.close()
    service.store.close()


def test_no_login_storm_and_pause(service, raw, clock, monkeypatch):
    calls = {"login": 0, "read": 0}
    def login():
        calls["login"] += 1
        return raw, clock.time()
    def read():
        calls["read"] += 1
        return raw, clock.time()
    monkeypatch.setattr(service.http, "login", login)
    monkeypatch.setattr(service.http, "read", read)
    service.cycle()
    for _ in range(3):
        clock.advance(15)
        service.cycle()
    assert calls == {"login": 1, "read": 3}
    service.store.command("pause", clock.time() + 1800)
    clock.advance(15)
    service.cycle()
    assert service.session.state == "PAUSED"
    assert calls["read"] == 3
    service.store.command("resume")
    service.cycle()
    assert calls["read"] == 4


@pytest.mark.parametrize("error", [RemoteError("network_error"), RemoteError("invalid_payload"), RemoteError("server_error"), Forbidden("forbidden_unclassified"), RemoteError("rate_limited", 600)])
def test_read_errors_preserve_session(service, clock, monkeypatch, error):
    service.session.success(clock.time())
    def fail():
        raise error
    monkeypatch.setattr(service.http, "read", fail)
    service.cycle()
    assert service.session.authenticated
    assert service.counters["logins"] == 0
    if error.retry_after:
        assert service.session.due >= clock.time() + error.retry_after


def test_logout_pending_login_cancelled_by_pause(service, clock, monkeypatch):
    service.session.success(clock.time())
    def fail():
        raise LoggedOut("session_lost")
    monkeypatch.setattr(service.http, "read", fail)
    service.cycle()
    assert service.session.due == clock.time() + 300
    service.store.command("pause", clock.time() + 1800)
    clock.advance(301)
    service.cycle()
    assert service.session.state == "PAUSED"
    assert service.counters["logins"] == 0


def test_bad_credentials_stop(service, clock, monkeypatch):
    def fail():
        raise InvalidCredentials("invalid_credentials")
    monkeypatch.setattr(service.http, "login", fail)
    service.cycle()
    clock.advance(100000)
    service.cycle()
    assert service.session.state == "AUTH_FAILED"
    assert service.counters["logins"] == 1


def test_no_catchup_after_slow_request(service, raw, clock, monkeypatch):
    def login():
        clock.advance(50)
        return raw, clock.time()
    monkeypatch.setattr(service.http, "login", login)
    service.cycle()
    assert service.next_poll == clock.time() + 15


def test_unavailable_sinks_do_not_stop_collection(service, raw, clock, monkeypatch):
    monkeypatch.setattr(service.http, "login", lambda: (raw, clock.time()))
    monkeypatch.setattr(service.http, "read", lambda: (raw, clock.time()))
    service.db.status = "retrying"
    service.mqtt.status = "retrying"
    for _ in range(3):
        service.cycle()
        clock.advance(15)
    assert service.counters["reads"] == 3
    assert service.store.stats()["items"] == 4  # three samples and login event
    assert service.mqtt.current()["values"]["s_t_spal"] == 222.1


def test_repeated_403_waits_without_authentication(service, clock, monkeypatch):
    service.session.success(clock.time())
    def forbidden():
        raise Forbidden("forbidden_unclassified")
    monkeypatch.setattr(service.http, "read", forbidden)
    for _ in range(3):
        service.cycle()
        clock.advance(15)
    assert service.session.authenticated
    assert service.session.due > clock.time() + 800
    assert service.counters["logins"] == 0

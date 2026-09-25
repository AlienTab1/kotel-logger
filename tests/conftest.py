import copy
import socket

import pytest

from kotel_reader.config import DEFAULTS
from kotel_reader.storage import Store


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return
    def blocked(*args, **kwargs):
        raise AssertionError("Unit tests must not access the network")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def cfg(tmp_path):
    config = copy.deepcopy(DEFAULTS)
    config["data_dir"] = str(tmp_path)
    config["postgres"]["enabled"] = config["mqtt"]["enabled"] = False
    config["signals"] = {"s_t_spal": {"type": "number", "publish": True, "name": "Spaliny", "device_class": "temperature"}}
    return config


@pytest.fixture
def store(tmp_path, cfg):
    db = Store(tmp_path, cfg["queue"])
    yield db
    db.close()


@pytest.fixture
def raw():
    return [{"Nazev": "s_t_spal", "Hodnota": 222.1, "Stav": "ok", "HodnotaF": "222,1 °C"}]


class FakeClock:
    def __init__(self, now=10000):
        self.now = now

    def advance(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("kotel_reader.service.utcnow", clock.time)
    monkeypatch.setattr("kotel_reader.service.time.monotonic", clock.time)
    monkeypatch.setattr("kotel_reader.http.utcnow", clock.time)
    return clock


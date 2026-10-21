import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from jer_usb import settings, service, usb_transport
from jer_usb.metrics import State


@pytest.mark.parametrize('changes', [dict(host='::1'), dict(port=0), dict(port=True),
    dict(stale_s=0), dict(passive_port='COM6'), dict(passive_port='/dev/x\nother'), dict(mode='control')])
def test_reject_bad_config(changes):
    with pytest.raises((ValueError, TypeError)):
        settings.validate(dict(passive_port='/dev/ttyACM0') | changes)


def test_health_is_independent_of_telemetry_and_disconnect():
    tick=[0]
    state=State(clock=lambda:tick[0])
    assert not state.healthy()
    state.progress()
    assert state.healthy()
    assert 'jer_usb_telemetry_available 0' in state.render()
    frame=bytes.fromhex(json.loads((Path(__file__).parent/'fixtures.json').read_text())[0])
    state.accept(frame)
    state.invalidate()
    assert 'jer_usb_slot_value' not in state.render()
    tick[0]=16
    assert not state.healthy()


def test_streaming_replay_does_not_refresh_at_eof(tmp_path, monkeypatch):
    frames=json.loads((Path(__file__).parent/'fixtures.json').read_text())
    path=tmp_path/'frames.jsonl'
    path.write_text('\n'.join(json.dumps(dict(direction='in',type=13,timestamp=i,hex=f))
                              for i,f in enumerate(frames[:2])))
    def forbidden(*args, **kwargs):
        raise AssertionError('Replay must not load the entire file')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(service, 'wait', lambda *a:None)
    tick=[0];state=State(clock=lambda:tick[0])
    service.replay(path,state,threading.Event())
    assert state.reads==2
    tick[0]=11
    assert 'jer_usb_slot_value' not in state.render()


def test_passive_transport_never_writes_and_closes(monkeypatch):
    events=[]
    class Port:
        def __init__(self, **kwargs):
            assert kwargs['baudrate']==115200 and kwargs['exclusive'] and kwargs['timeout']==1
        def open(self):
            assert self.dtr is False and self.rts is False
            events.append('open')
        def read(self,n):
            assert n==512
            return b''
        def close(self):events.append('close')
        def write(self,*a):raise AssertionError('Unexpected USB application write')
    monkeypatch.setitem(sys.modules,'serial',SimpleNamespace(Serial=Port))
    with pytest.raises(RuntimeError):
        with usb_transport.open_passive('/dev/ttyACM0') as read:
            assert read()==b''
            raise RuntimeError('Simulated exit')
    assert events==['open','close']


def test_receive_fragments_and_idle_cycles():
    frames=json.loads((Path(__file__).parent/'fixtures.json').read_text())
    frame=bytes.fromhex(frames[0]);stop=threading.Event();state=State()
    chunks=iter([b'',frame[:10],frame[10:]])
    def read():
        try:return next(chunks)
        except StopIteration:stop.set();return b''
    service.receive(read,state,stop)
    assert state.reads==1 and state.healthy()


def test_http_health_and_metrics():
    tick=[0];state=State(clock=lambda:tick[0]);state.progress()
    server,thread=service.serve(state,'127.0.0.1',0)
    client=build_opener(ProxyHandler({}))
    address=f'http://127.0.0.1:{server.server_port}'
    try:
        with client.open(address+'/healthz',timeout=3) as response:
            assert response.status==200
        with client.open(address+'/metrics',timeout=3) as response:
            assert b'jer_usb_telemetry_available 0' in response.read()
        tick[0]=16
        with pytest.raises(HTTPError) as error:client.open(address+'/healthz',timeout=3)
        assert error.value.code==503
    finally:
        server.shutdown();server.server_close();thread.join(timeout=4)
    assert not thread.is_alive()

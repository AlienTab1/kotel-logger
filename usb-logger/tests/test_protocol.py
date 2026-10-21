import json, sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from jer_usb.protocol import Stream, checksum, slots
from jer_usb.metrics import State

FIXTURE=Path(__file__).parent/'fixtures.json'

def frames():return [bytes.fromhex(s) for s in json.loads(FIXTURE.read_text())]

def test_all_chunk_boundaries():
    source=frames();data=b''.join(source)
    for size in (1,2,3,7,64,187,259,512):
        parser=Stream();out=[]
        for start in range(0,len(data),size):out.extend(parser.feed(data[start:start+size]))
        assert out==source and not parser.buffer and parser.discarded==0

def test_corruption_and_special_slots():
    f=frames()[0];assert checksum(f)
    bad=bytearray(f);bad[26]^=1
    with pytest.raises(ValueError,match='checksum'):slots(bytes(bad))
    assert slots(f)[0]['value'] is None
    assert slots(f)[1]['value']==39.2
    for f in frames()[1:]:
        for s in slots(f):
            if s['tag'] not in (33,34) or s['tag']==34 and s['raw'] not in (0,1):
                assert s['value'] is None

def test_stale_independent_blocks():
    tick=[0];state=State(clock=lambda:tick[0]);state.accept(frames()[0])
    assert 'jer_usb_slot_value' in state.render()
    assert 'jer_usb_telemetry_available 0' in state.render()
    tick[0]=11
    assert 'jer_usb_slot_value' not in state.render()

def test_invalid_length_and_unknown_message_are_rejected():
    f=bytearray(frames()[0]);f[3]-=1
    with pytest.raises(ValueError,match='length'):slots(bytes(f))
    f=bytearray(frames()[0]);f[4]=11
    f[-2:]=((~sum(f[:-2]))&65535).to_bytes(2,'big')
    with pytest.raises(ValueError,match='telemetry'):slots(bytes(f))

def test_one_fresh_block_cannot_refresh_another():
    tick=[0];state=State(clock=lambda:tick[0]);source=frames()
    state.accept(source[0]);state.accept(source[2])
    assert 'jer_usb_telemetry_available 1' in state.render()
    tick[0]=11;state.accept(source[2])
    result=state.render()
    assert 'jer_usb_telemetry_available 0' in result
    assert 'jer_usb_slot_value{block="0x0d"' not in result
    assert 'jer_usb_slot_value{block="0x14"' in result

def test_real_capture_inventory():
    from jer_usb.capture import transfers
    capture=Path(__file__).resolve().parents[1]/'log/capture01/capture01.pcapng'
    if not capture.exists():pytest.skip('Private capture not distributed; sanitized fixture tests still run')
    streams={};result=[]
    for p in transfers(capture):result.extend(streams.setdefault(p['endpoint'],Stream()).feed(p['data']))
    assert len(result)==1553
    assert all(checksum(f) for f in result)
    assert all(not s.buffer and not s.discarded for s in streams.values())

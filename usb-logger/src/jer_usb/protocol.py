"""Framing and typed slots; semantic mapping lives separately."""
import struct

MAGIC = b'\x28\x93'
HEADER_SIZE = 21

class Stream:
    def __init__(self):
        self.buffer = bytearray()
        self.discarded = 0
        self.consumed = 0

    def feed(self, chunk):
        self.buffer.extend(chunk)
        result = []
        while len(self.buffer) >= 4:
            if self.buffer[:2] != MAGIC:
                del self.buffer[0]
                self.discarded += 1
                self.consumed += 1
                continue
            length = int.from_bytes(self.buffer[2:4], 'big')
            if not 23 <= length <= 4096:
                del self.buffer[0]
                self.discarded += 1
                self.consumed += 1
                continue
            if len(self.buffer) < length: break
            result.append(bytes(self.buffer[:length]))
            del self.buffer[:length]
            self.consumed += length
        return result

def checksum(frame):
    # 16-bit one's complement of byte sum, stored big endian.
    return ((~sum(frame[:-2])) & 0xffff) == int.from_bytes(frame[-2:], 'big')

def slots(frame):
    if frame[:2] != MAGIC or int.from_bytes(frame[2:4], 'big') != len(frame):
        raise ValueError('Invalid frame length/magic')
    if not checksum(frame): raise ValueError('Invalid checksum')
    if (frame[4],len(frame)) not in ((13,259),(20,187)):
        raise ValueError('Not a known telemetry response')
    result=[]
    for index, offset in enumerate(range(21,len(frame)-2,4)):
        tag, raw = struct.unpack_from('<HH', frame, offset)
        value = raw/10 if tag == 0x21 else raw if tag == 0x22 and raw in (0,1) else None
        result.append(dict(index=index, offset=offset, tag=tag, raw=raw, value=value,
                           valid=value is not None))
    return result

"""Strict PCAPNG/USBPcap reader. Only link type 249 is supported."""
import struct
from pathlib import Path

def packets(path):
    data = Path(path).read_bytes()
    pos = number = 0
    endian = '<'
    interfaces = []
    while pos < len(data):
        if len(data)-pos < 12:
            raise ValueError('Truncated block header')
        if data[pos:pos+4] == b'\x0a\x0d\x0d\x0a':
            endian = '<' if data[pos+8:pos+12] == b'\x4d\x3c\x2b\x1a' else '>'
            interfaces = []
        kind, size = struct.unpack_from(endian+'II', data, pos)
        if size < 12 or size % 4 or pos+size > len(data):
            raise ValueError('Invalid block length')
        if struct.unpack_from(endian+'I', data, pos+size-4)[0] != size:
            raise ValueError('Block trailer mismatch')
        body = data[pos+8:pos+size-4]
        if kind == 1:
            link = struct.unpack_from(endian+'H', body)[0]
            resolution = 1e-6
            offset = 0
            cursor = 8
            while cursor+4 <= len(body):
                code, length = struct.unpack_from(endian+'HH', body, cursor)
                value = body[cursor+4:cursor+4+length]
                if code == 9 and length == 1:
                    resolution = (2 if value[0]&128 else 10)**(-(value[0]&127))
                if code == 14 and length == 8:
                    offset = struct.unpack(endian+'q', value)[0]
                cursor += 4+(length+3)//4*4
                if code == 0: break
            interfaces.append((link, resolution, offset))
        elif kind == 6:
            number += 1
            iface, hi, lo, cap, original = struct.unpack_from(endian+'IIIII', body)
            link, resolution, offset = interfaces[iface]
            if cap != original or cap > len(body)-20:
                raise ValueError(f'Truncated packet {number}')
            raw = body[20:20+cap]
            if link != 249:
                raise ValueError(f'Unsupported link type {link}')
            if len(raw) < 27: raise ValueError('Short USBPcap header')
            h, irp, status, function, info, bus, device, endpoint, transfer, length = struct.unpack_from('<HQIHBHHBBI', raw)
            if h < 27 or h > len(raw) or length != len(raw)-h:
                raise ValueError(f'USBPcap payload length mismatch {number}')
            yield dict(packet=number, timestamp=((hi<<32)|lo)*resolution+offset,
                       bus=bus, device=device, endpoint=endpoint, transfer=transfer,
                       info=info, status=status, function=function, data=raw[h:], irp=irp)
        pos += size

def transfers(path, device=5):
    for p in packets(path):
        # OUT submit, IN complete only; empty completions are not duplicate payloads.
        if (p['device'] == device and p['transfer'] == 3 and p['status'] == 0
                and p['data'] and (p['endpoint'], p['info']&1) in ((5,0),(130,1))):
            yield p

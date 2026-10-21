"""Passive CDC transport for commissioning; no protocol writes are exposed.

Opening CDC still performs line configuration. Polling/handshake are deliberately
not implemented until their session semantics have been tested on the SBC.
"""
from contextlib import contextmanager

@contextmanager
def open_passive(path):
    import serial
    port = serial.Serial(port=None, baudrate=115200, timeout=1,
                         write_timeout=1, exclusive=True)
    port.dtr = False
    port.rts = False
    port.port = path
    try:
        port.open()
        yield lambda: port.read(512)
    finally:
        port.close()

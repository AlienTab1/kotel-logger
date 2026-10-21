"""Diagnostic slot exporter. Unverified semantic aliases are never published."""
import threading, time
from .protocol import slots

class State:
    def __init__(self, stale_s=10, clock=time.monotonic):
        self.clock, self.stale_s = clock, stale_s
        self.blocks = {}
        self.lock = threading.Lock()
        self.errors = self.reads = self.reconnects = 0
        self.progress_at = None

    def progress(self):
        with self.lock:
            self.progress_at = self.clock()

    def healthy(self):
        with self.lock:
            return self.progress_at is not None and 0 <= self.clock()-self.progress_at < 15

    def invalidate(self):
        with self.lock:
            self.blocks.clear()

    def error(self, reconnect=False):
        with self.lock:
            if reconnect:
                self.reconnects += 1
            else:
                self.errors += 1

    def accept(self, frame):
        decoded = slots(frame)
        with self.lock:
            self.blocks[frame[4]]=(self.clock(),time.time(),decoded)
            self.reads += 1

    def render(self):
        with self.lock:
            now=self.clock()
            lines=[f'jer_usb_reads_total {self.reads}', f'jer_usb_protocol_errors_total {self.errors}',
                   f'jer_usb_reconnects_total {self.reconnects}']
            healthy = self.progress_at is not None and 0 <= now-self.progress_at < 15
            lines.append(f'jer_usb_collector_healthy {int(healthy)}')
            lines.append('jer_usb_build_info{version="0.1.1"} 1')
            fresh=[]
            for block in (13,20):
                sample=self.blocks.get(block)
                available=sample is not None and 0<=now-sample[0]<=self.stale_s
                fresh.append(available)
                label=f'block="0x{block:02x}"'
                lines.append(f'jer_usb_block_available{{{label}}} {int(available)}')
                if sample is None: continue
                lines.append(f'jer_usb_receive_age_seconds{{{label}}} {max(0,now-sample[0])}')
                lines.append(f'jer_usb_last_received_timestamp_seconds{{{label}}} {sample[1]}')
                for slot in sample[2]:
                    labels=label+f',index="{slot["index"]}"'
                    valid=available and slot['valid']
                    lines.append(f'jer_usb_slot_available{{{labels}}} {int(valid)}')
                    if valid: lines.append(f'jer_usb_slot_value{{{labels}}} {slot["value"]}')
            lines.append(f'jer_usb_telemetry_available {int(all(fresh))}')
            return '\n'.join(lines)+'\n'

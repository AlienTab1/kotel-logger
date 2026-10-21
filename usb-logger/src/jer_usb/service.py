"""Low-memory offline replay/passive receiver; no application protocol writes."""
import argparse
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from . import settings
from .metrics import State
from .protocol import Stream
from .usb_transport import open_passive


def log(event, **fields):
    print(json.dumps(dict(timestamp=time.time(), event=event, **fields)), flush=True)


class MetricsServer(HTTPServer):
    # One HTTP worker and bounded socket waits keep RAM use predictable on Pi 1.
    allow_reuse_address = True

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(3)
        return connection, address


def serve(state, host, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/metrics':
                code, body = 200, state.render()
            elif self.path == '/healthz':
                code = 200 if state.healthy() else 503
                body = 'worker responsive\n' if code == 200 else 'worker stalled\n'
            else:
                self.send_error(404)
                return
            payload = body.encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/plain; version=0.0.4')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = MetricsServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def wait(seconds, state, stop):
    """Responsive waiting does not mean fresh telemetry."""
    deadline = time.monotonic() + seconds
    while not stop.is_set():
        state.progress()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stop.wait(min(1, remaining))


def replay(path, state, stop):
    # Stream JSONL; never load the entire capture report into Pi 1 memory.
    previous = None
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            if stop.is_set():
                return
            row = json.loads(line)
            if row['direction'] != 'in' or row['type'] not in (13, 20):
                continue
            if previous is not None:
                wait(max(0, row['timestamp']-previous), state, stop)
            if stop.is_set():
                return
            state.accept(bytes.fromhex(row['hex']))
            state.progress()
            previous = row['timestamp']
    log('replay_complete')


def receive(read, state, stop):
    stream = Stream()
    while not stop.is_set():
        chunk = read()  # Transport configures a one-second timeout.
        state.progress()
        for frame in stream.feed(chunk):
            if frame[4] not in (13, 20):
                continue
            try:
                state.accept(frame)
            except ValueError:
                state.error()


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--config', type=Path, help='Passive-mode JSON configuration')
    modes.add_argument('--replay', type=Path)
    modes.add_argument('--passive-port')
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', type=int, default=None)
    parser.add_argument('--stale-s', type=int, default=None)
    args = parser.parse_args(argv)
    config = settings.load(args.config) if args.config else dict(settings.DEFAULTS)
    for key in ('host', 'port', 'stale_s'):
        value = getattr(args, key)
        if value is not None:
            config[key] = value
    if args.passive_port:
        config['passive_port'] = args.passive_port
    # Replay validates listener settings without opening this placeholder.
    checked = settings.validate({'passive_port': '/dev/replay', **config})
    return args, checked


def main(argv=None):
    args, config = arguments(argv)
    if not args.replay:
        # A missing dependency is an installation error, not an endless reconnect.
        import serial  # noqa: F401
    state = State(stale_s=config['stale_s'])
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stop.set())
    state.progress()
    server, thread = serve(state, config['host'], config['port'])
    log('started', mode='replay' if args.replay else 'passive',
        host=config['host'], port=config['port'])
    try:
        if args.replay:
            replay(args.replay, state, stop)
            # Do not repeat or rejuvenate EOF readings. Keep scrape/health reachable.
            while not stop.is_set():
                wait(1, state, stop)
        else:
            while not stop.is_set():
                try:
                    with open_passive(config['passive_port']) as read:
                        state.invalidate()
                        log('serial_open')
                        receive(read, state, stop)
                except OSError as exc:
                    state.invalidate()
                    state.error(reconnect=True)
                    log('serial_error', error=type(exc).__name__, errno=exc.errno)
                    wait(5, state, stop)
    finally:
        state.invalidate()
        server.shutdown()
        server.server_close()
        thread.join(timeout=4)
        log('stopped')


if __name__ == '__main__':
    main()

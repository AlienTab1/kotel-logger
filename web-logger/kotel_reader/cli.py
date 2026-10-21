from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import load
from .model import utcnow
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Read-only Thematronic collector: Prometheus + MQTT")
    parser.add_argument("--config", default="/config/config.yaml")
    parser.add_argument("--data-dir", default="/data", help="Pro status/pause/resume/snapshot/healthcheck")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "validate", "status", "resume", "snapshot", "healthcheck"):
        sub.add_parser(command)
    pause = sub.add_parser("pause")
    pause.add_argument("--minutes", type=float, default=30)
    args = parser.parse_args()
    from .service import JsonLog
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLog())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    # HTTPX INFO includes full URLs (including redirect query parameters). Use our
    # sanitized phase/status diagnostics instead of third-party transport logging.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.command in ("run", "validate"):
            cfg = load(args.config)
            if args.command == "validate":
                print("Configuration is valid; connectivity and passwords have not been verified.")
            else:
                from .service import run
                run(cfg)
            return
        if not (Path(args.data_dir) / "state.sqlite").exists():
            raise ValueError("The data directory does not contain collector state yet")
        store = Store(args.data_dir)
        try:
            if args.command == "status":
                print(json.dumps(store.get("status", {}), ensure_ascii=False, indent=2))
            elif args.command == "healthcheck":
                state = store.get("status", {})
                ok = 0 <= utcnow() - state.get("heartbeat", 0) < 120 and state.get("state") != "STOPPED" and state.get("workers_alive", False)
                sys.exit(0 if ok else 1)
            elif args.command == "snapshot":
                print(json.dumps(store.get("latest_snapshot"), ensure_ascii=False, indent=2))
            else:
                if args.command == "pause" and not 0 < args.minutes <= 525600:
                    raise ValueError("Pause must be greater than zero and no longer than one year")
                store.command(args.command, utcnow() + args.minutes * 60 if args.command == "pause" else None)
                print("Command saved; the collector will apply it after the current HTTP request completes.")
        finally:
            store.close()
    except Exception as exc:
        # Configuration errors contain field names only. Never print HTTP/DB exception bodies.
        from filelock import Timeout
        if isinstance(exc, Timeout):
            print("Another instance is already using this data directory.", file=sys.stderr)
        elif isinstance(exc, ValueError):
            print(f"Chyba konfigurace: {exc}", file=sys.stderr)
        else:
            print(f"Operation failed ({type(exc).__name__}); check configuration, permissions and service availability.", file=sys.stderr)
        sys.exit(1)

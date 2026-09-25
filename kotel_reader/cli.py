from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULTS, load
from .model import utcnow
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="Čtecí služba Thematronic → PostgreSQL + MQTT")
    parser.add_argument("--config", default="/config/config.yaml")
    parser.add_argument("--data-dir", default="/data", help="Pro status/pause/resume/export/healthcheck")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "validate", "status", "resume", "export", "healthcheck"):
        sub.add_parser(command)
    pause = sub.add_parser("pause")
    pause.add_argument("--minutes", type=float, default=30)
    migration = sub.add_parser("migrate")
    migration.add_argument("--directory", default="/app/migrations")
    args = parser.parse_args()
    from .service import JsonLog
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLog())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        if args.command in ("run", "validate", "migrate"):
            cfg = load(args.config)
            if args.command == "validate":
                print("Konfigurace je platná; připojení ani hesla nebyla ověřena.")
            elif args.command == "migrate":
                from .postgres import migrate
                migrate(cfg["postgres"], args.directory)
                print("Migrace dokončeny.")
            else:
                from .service import run
                run(cfg)
            return
        if not (Path(args.data_dir) / "queue.sqlite").exists():
            raise ValueError("Datový adresář dosud neobsahuje stav collectoru")
        store = Store(args.data_dir, DEFAULTS["queue"])
        try:
            if args.command == "status":
                print(json.dumps(store.get("status", {}), ensure_ascii=False, indent=2))
            elif args.command == "healthcheck":
                state = store.get("status", {})
                ok = 0 <= utcnow() - state.get("heartbeat", 0) < 120 and state.get("state") != "STOPPED" and state.get("workers_alive", False)
                sys.exit(0 if ok else 1)
            elif args.command == "export":
                store.export(sys.stdout)
            else:
                if args.command == "pause" and not 0 < args.minutes <= 525600:
                    raise ValueError("Pauza musí být > 0 a nejvýše jeden rok")
                store.command(args.command, utcnow() + args.minutes * 60 if args.command == "pause" else None)
                print("Příkaz uložen; collector jej převezme po dokončení probíhajícího HTTP požadavku.")
        finally:
            store.close()
    except Exception as exc:
        # Configuration errors contain field names only. Never print HTTP/DB exception bodies.
        from filelock import Timeout
        if isinstance(exc, Timeout):
            print("Jiná instance již používá tento datový adresář.", file=sys.stderr)
        elif isinstance(exc, ValueError):
            print(f"Chyba konfigurace: {exc}", file=sys.stderr)
        else:
            print(f"Operace selhala ({type(exc).__name__}); ověřte konfiguraci, oprávnění a dostupnost služeb.", file=sys.stderr)
        sys.exit(1)


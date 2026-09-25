from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .config import secret


def connect(cfg, migration=False):
    return psycopg.connect(host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"],
                           user=secret("POSTGRES_MIGRATION_USER", True) if migration else cfg["user"],
                           password=secret("POSTGRES_MIGRATION_PASSWORD" if migration else "POSTGRES_PASSWORD"),
                           sslmode=cfg["sslmode"], connect_timeout=10,
                           options="-c statement_timeout=15000 -c lock_timeout=5000")


def write(conn, kind, item):
    with conn.transaction():
        if kind == "snapshot":
            conn.execute("""INSERT INTO kotel.snapshots
                (sample_id,device_id,received_at,source_timestamp,raw,"values",statuses)
                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(sample_id) DO NOTHING""",
                (item["sample_id"], item["device_id"], item["received_at"], item["source_timestamp"],
                 Jsonb(item["raw"]), Jsonb(item["values"]), Jsonb(item["statuses"])))
        else:
            conn.execute("""INSERT INTO kotel.session_events(event_id,device_id,occurred_at,event,details)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING""",
                (item["sample_id"], item["device_id"], item["occurred_at"], item["event"], Jsonb(item["details"])))


class DatabaseWorker:
    def __init__(self, cfg, store, stop):
        self.cfg, self.store, self.stop = cfg, store, stop
        self.status = "disabled" if not cfg["enabled"] else "connecting"
        self.last_progress = time.monotonic()

    def run(self):
        if not self.cfg["enabled"]:
            return
        delay, maintenance = 1, 0
        while not self.stop.is_set():
            self.last_progress = time.monotonic()
            try:
                with connect(self.cfg) as conn:
                    self.status = "connected"
                    while not self.stop.is_set():
                        self.last_progress = time.monotonic()
                        row = self.store.peek()
                        if row:
                            seq, kind, item = row
                            write(conn, kind, item)
                            conn.commit()  # commit MUST precede the local acknowledgement
                            self.store.ack(seq)
                            delay = 1
                        else:
                            self.stop.wait(1)
                        if time.monotonic() >= maintenance:
                            # Bounded batches: never lock/delete the entire retention range at once.
                            with conn.transaction():
                                for table, column in (("snapshots", "received_at"), ("session_events", "occurred_at")):
                                    conn.execute(f"DELETE FROM kotel.{table} WHERE ctid IN (SELECT ctid FROM kotel.{table} WHERE {column} < now() - make_interval(days => %s) LIMIT 10000)", (self.cfg["retention_days"],))
                            conn.commit()
                            maintenance = time.monotonic() + 60
            except Exception:
                # Never log exception strings: driver diagnostics may contain credentials or DSNs.
                self.status = "retrying"
                logging.warning("postgres_unavailable")
                self.stop.wait(delay * random.uniform(1, 1.1))
                delay = min(delay * 2, 300)


def migrate(cfg, directory):
    files = sorted(Path(directory).glob("*.sql"))
    if not files:
        raise ValueError("Migration directory contains no SQL files")
    with connect(cfg, migration=True) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(2985001)")
        conn.execute("CREATE TABLE IF NOT EXISTS public.kotel_migrations(name text PRIMARY KEY)")
        for path in files:
            if not conn.execute("SELECT 1 FROM public.kotel_migrations WHERE name=%s", (path.name,)).fetchone():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.kotel_migrations VALUES (%s)", (path.name,))

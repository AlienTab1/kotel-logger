from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class Store:
    """Local control state and one diagnostic snapshot, never a telemetry backlog."""
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(directory / "state.sqlite"), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (seq INTEGER PRIMARY KEY AUTOINCREMENT,
              action TEXT NOT NULL, until REAL);
        """)
        self.db.commit()
        # Import cooldown/pause once when upgrading. Never modify or replay the old queue.
        legacy = directory / "queue.sqlite"
        if legacy.exists() and not self.get("legacy_imported", False):
            keys = ("next_attempt", "saved_at", "wait_reason", "paused_until", "counters")
            old = sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                rows = old.execute("SELECT key,value FROM state").fetchall()
                with self.db:
                    for key, value in rows:
                        if key in keys:
                            self.db.execute("INSERT OR IGNORE INTO state VALUES (?,?)", (key, value))
                    self.db.execute("INSERT OR REPLACE INTO state VALUES ('legacy_imported','true')")
            finally:
                old.close()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def command(self, action, until=None):
        if action not in ("pause", "resume"):
            raise ValueError("Unsupported local command")
        with self.lock, self.db:
            if self.db.execute("SELECT count(*) FROM commands").fetchone()[0] >= 100:
                raise ValueError("Command queue full")
            self.db.execute("INSERT INTO commands(action,until) VALUES (?,?)", (action, until))

    def apply_commands(self):
        with self.lock, self.db:
            rows = self.db.execute("SELECT seq,action,until FROM commands ORDER BY seq").fetchall()
            for _, action, until in rows:
                self.db.execute("INSERT INTO state VALUES ('paused_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (json.dumps(until if action == "pause" else 0),))
            if rows:
                self.db.execute("DELETE FROM commands WHERE seq<=?", (rows[-1][0],))
            return [(action, until) for _, action, until in rows]

    def close(self):
        with self.lock:
            self.db.close()

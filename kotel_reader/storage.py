from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


class Store:
    """One durable queue shared by workers; no SQLite operation crosses a network call."""
    def __init__(self, directory, limits):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.limits = limits
        self.db = sqlite3.connect(str(Path(directory) / "queue.sqlite"), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS queue (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
              kind TEXT NOT NULL, created REAL NOT NULL, payload TEXT NOT NULL, bytes INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (seq INTEGER PRIMARY KEY AUTOINCREMENT,
              action TEXT NOT NULL, until REAL);
        """)
        self.db.commit()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))

    def enqueue(self, kind, item, now):
        payload = json.dumps(item, ensure_ascii=False, allow_nan=False)
        size = len(payload.encode("utf-8"))
        with self.lock, self.db:
            dropped = self._prune(now)
            if size > self.limits["max_bytes"]:
                dropped += 1
            else:
                self.db.execute("INSERT OR IGNORE INTO queue(id,kind,created,payload,bytes) VALUES (?,?,?,?,?)",
                                (item["sample_id"], kind, now, payload, size))
                count, total = self.db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM queue").fetchone()
                while count > self.limits["max_items"] or total > self.limits["max_bytes"]:
                    seq, n = self.db.execute("SELECT seq,bytes FROM queue ORDER BY seq LIMIT 1").fetchone()
                    self.db.execute("DELETE FROM queue WHERE seq=?", (seq,))
                    count, total, dropped = count - 1, total - n, dropped + 1
            if dropped:
                self._loss(dropped)
            return dropped

    def _loss(self, count):
        row = self.db.execute("SELECT value FROM state WHERE key='lost_items'").fetchone()
        value = int(json.loads(row[0])) if row else 0
        self.db.execute("INSERT INTO state VALUES ('lost_items',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(value + count),))

    def _prune(self, now):
        return self.db.execute("DELETE FROM queue WHERE created<?", (now - self.limits["max_age_s"],)).rowcount

    def prune(self, now):
        with self.lock, self.db:
            dropped = self._prune(now)
            if dropped:
                self._loss(dropped)
            return dropped

    def peek(self):
        with self.lock:
            row = self.db.execute("SELECT seq,kind,payload FROM queue ORDER BY seq LIMIT 1").fetchone()
            return (row[0], row[1], json.loads(row[2])) if row else None

    def ack(self, seq):
        with self.lock, self.db:
            self.db.execute("DELETE FROM queue WHERE seq=?", (seq,))

    def stats(self):
        with self.lock:
            count, size = self.db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM queue").fetchone()
            return {"items": count, "bytes": size, "lost_items": self.get("lost_items", 0)}

    def command(self, action, until=None):
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

    def export(self, stream):
        with self.lock:
            for kind, payload in self.db.execute("SELECT kind,payload FROM queue ORDER BY seq"):
                stream.write(json.dumps({"kind": kind, "data": json.loads(payload)}, ensure_ascii=False) + "\n")

    def close(self):
        with self.lock:
            self.db.close()

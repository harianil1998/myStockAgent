"""SQLite persistence: signals, source state (cursors/snapshots), recommendations."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Signal, SignalKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    ticker TEXT NOT NULL,
    kind TEXT NOT NULL,
    sentiment REAL NOT NULL,
    weight REAL NOT NULL,
    text TEXT,
    url TEXT,
    author TEXT,
    ts TEXT NOT NULL,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker, ts);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    run_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add_signals(self, signals: list[Signal]) -> list[Signal]:
        """Insert signals, returning only the ones that were new."""
        new = []
        with self._lock, self.conn:
            for s in signals:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (s.id, s.source, s.ticker, s.kind.value, s.sentiment, s.weight, s.text[:2000],
                     s.url, s.author, s.timestamp.isoformat(), json.dumps(s.extra, default=str)),
                )
                if cur.rowcount:
                    new.append(s)
        return new

    def signals_since(self, since: datetime) -> list[Signal]:
        rows = self.conn.execute(
            "SELECT id, source, ticker, kind, sentiment, weight, text, url, author, ts, extra "
            "FROM signals WHERE ts >= ? ORDER BY ts", (since.isoformat(),)
        ).fetchall()
        return [
            Signal(id=r[0], source=r[1], ticker=r[2], kind=SignalKind(r[3]), sentiment=r[4], weight=r[5],
                   text=r[6] or "", url=r[7] or "", author=r[8] or "",
                   timestamp=datetime.fromisoformat(r[9]), extra=json.loads(r[10] or "{}"))
            for r in rows
        ]

    def mention_counts(self, start: datetime, end: datetime) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT ticker, COUNT(*) FROM signals WHERE ts >= ? AND ts < ? AND kind IN ('social','news') "
            "GROUP BY ticker", (start.isoformat(), end.isoformat())
        ).fetchall()
        return dict(rows)

    def get_state(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value, default=str)))

    def save_recommendations(self, mode: str, payload: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO recommendations VALUES (?,?,?)",
                (datetime.now(timezone.utc).isoformat(), mode, json.dumps(payload, default=str)),
            )

    def prune(self, keep_days: int = 30) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM signals WHERE ts < ?", (cutoff,))

"""SQLite store: one shared connection behind a lock, WAL, tiny migration runner."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


def _v1(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE matches (
            series_key TEXT NOT NULL,
            season     INTEGER NOT NULL,        -- -1 = the whole series
            mal_id     INTEGER,
            anilist_id INTEGER,
            title      TEXT NOT NULL DEFAULT '',
            source     TEXT NOT NULL DEFAULT 'auto',   -- auto | manual
            PRIMARY KEY (series_key, season)
        );
        CREATE TABLE overrides (
            series_key TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            label      TEXT,
            include    INTEGER,
            PRIMARY KEY (series_key, episode_id)
        );
        CREATE TABLE trims (
            series_key TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            kind       TEXT NOT NULL,           -- intro | outro | recap | preview
            start      REAL,                    -- NULL start/end = cleared on purpose
            end        REAL,
            source     TEXT NOT NULL DEFAULT 'auto',   -- auto | user
            PRIMARY KEY (series_key, episode_id, kind)
        );
        CREATE TABLE series_settings (
            series_key TEXT PRIMARY KEY,
            settings   TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE http_cache (
            key        TEXT PRIMARY KEY,
            body       TEXT NOT NULL,
            fetched_at REAL NOT NULL
        );
        CREATE TABLE probe_cache (
            path   TEXT PRIMARY KEY,
            mtime  REAL NOT NULL,
            size   INTEGER NOT NULL,
            info   TEXT NOT NULL
        );
        CREATE TABLE jobs (
            id         TEXT PRIMARY KEY,
            kind       TEXT NOT NULL,
            title      TEXT NOT NULL DEFAULT '',
            status     TEXT NOT NULL DEFAULT 'queued',  -- queued|running|done|failed|cancelled
            progress   REAL NOT NULL DEFAULT 0,
            message    TEXT NOT NULL DEFAULT '',
            payload    TEXT NOT NULL DEFAULT '{}',
            result     TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)


MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [_v1]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            have = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, step in enumerate(MIGRATIONS[have:], start=have + 1):
                step(self._conn)
                self._conn.execute(f"PRAGMA user_version = {i}")
                self._conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))
            self._conn.commit()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))

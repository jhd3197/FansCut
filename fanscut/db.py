"""SQLite store: one shared connection behind a lock, WAL, tiny migration runner.

Statements autocommit; wrap several in transaction() to make them one unit."""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any


def _v1() -> str:
    return """
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
    """


def _v3() -> str:
    return """
        CREATE TABLE custom_segments (
            series_key TEXT NOT NULL,
            episode_id TEXT NOT NULL,
            ranges     TEXT NOT NULL DEFAULT '[]',   -- JSON [[start, end], ...] in seconds
            PRIMARY KEY (series_key, episode_id)
        );
    """


def _v2() -> str:
    return """
        CREATE TABLE renders (
            hash       TEXT PRIMARY KEY,        -- content hash of the render inputs
            series_key TEXT NOT NULL,
            title      TEXT NOT NULL DEFAULT '',
            path       TEXT NOT NULL,
            mode       TEXT NOT NULL DEFAULT '',        -- copy | encode
            keep       INTEGER NOT NULL DEFAULT 1,      -- 0 = evict after render.ephemeral_days
            size_bytes INTEGER NOT NULL DEFAULT 0,
            job_id     TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
    """


def _v4() -> str:
    return """
        CREATE TABLE fingerprint_cache (
            key        TEXT PRIMARY KEY,        -- file identity + fingerprint settings
            head       BLOB NOT NULL,           -- float32 feature frames, head_rows x 40
            head_rows  INTEGER NOT NULL,
            tail       BLOB NOT NULL,
            tail_rows  INTEGER NOT NULL,
            tail_start REAL NOT NULL,
            duration   REAL NOT NULL
        );
    """


def _v5() -> str:
    return """
        CREATE TABLE timelines (
            id         TEXT PRIMARY KEY,
            series_key TEXT NOT NULL,
            title      TEXT NOT NULL,
            doc        TEXT NOT NULL,            -- the validated recipe JSON
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE timeline_syncs (
            timeline_id TEXT PRIMARY KEY,
            report      TEXT NOT NULL,           -- sync report JSON (offsets, confidence)
            updated_at  TEXT NOT NULL
        );
        CREATE TABLE anchor_cache (
            key   TEXT PRIMARY KEY,              -- file identity + position + kind
            value TEXT NOT NULL                  -- "a1:<base64>" or "v1:<base64>"
        );
    """


def _v6() -> str:
    return """
        CREATE TABLE file_checks (
            path   TEXT NOT NULL,
            kind   TEXT NOT NULL,                  -- tail | crop
            mtime  REAL NOT NULL,
            size   INTEGER NOT NULL,
            result TEXT NOT NULL,                  -- JSON
            PRIMARY KEY (path, kind)
        );
    """


def _v7() -> str:
    return """
        CREATE TABLE installs (
            timeline_id  TEXT PRIMARY KEY,
            repo         TEXT NOT NULL,            -- owner/name on GitHub
            ref          TEXT NOT NULL,            -- branch it follows
            sha          TEXT NOT NULL,            -- commit the timeline came from
            poster       TEXT NOT NULL DEFAULT '', -- local poster file, '' = none
            installed_at TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
    """


def _v8() -> str:
    # Index the queue's hot query; let a deleted timeline take its sync report
    # and install record with it (they used to be left behind).
    return """
        CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at);
        ALTER TABLE jobs ADD COLUMN lane TEXT NOT NULL DEFAULT 'light';
        UPDATE jobs SET lane = 'heavy' WHERE kind IN ('render', 'timeline_render');
        CREATE TABLE timeline_syncs_new (
            timeline_id TEXT PRIMARY KEY REFERENCES timelines(id) ON DELETE CASCADE,
            report      TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        INSERT INTO timeline_syncs_new SELECT s.* FROM timeline_syncs s
            WHERE s.timeline_id IN (SELECT id FROM timelines);
        DROP TABLE timeline_syncs;
        ALTER TABLE timeline_syncs_new RENAME TO timeline_syncs;
        CREATE TABLE installs_new (
            timeline_id  TEXT PRIMARY KEY REFERENCES timelines(id) ON DELETE CASCADE,
            repo         TEXT NOT NULL,
            ref          TEXT NOT NULL,
            sha          TEXT NOT NULL,
            poster       TEXT NOT NULL DEFAULT '',
            installed_at TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        INSERT INTO installs_new SELECT i.* FROM installs i
            WHERE i.timeline_id IN (SELECT id FROM timelines);
        DROP TABLE installs;
        ALTER TABLE installs_new RENAME TO installs;
    """


def _v9() -> str:
    # What FansCut put into Plex: rendered cuts (found after Plex scans them) and
    # watch-order playlists, so My cuts can list playlists and open either in Plex.
    return """
        CREATE TABLE plex_items (
            kind        TEXT NOT NULL,             -- 'cut' or 'playlist'
            ref         TEXT NOT NULL,             -- timeline id, or the suggestion id
            rating_key  TEXT NOT NULL,             -- Plex's id for the item
            title       TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT '{}',-- JSON: films for a playlist
            created_at  TEXT NOT NULL,
            PRIMARY KEY (kind, ref)
        );
    """


MIGRATIONS: list[Callable[[], str]] = [_v1, _v2, _v3, _v4, _v5, _v6, _v7, _v8, _v9]


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0  # nesting of transaction() on the thread holding the lock
        # isolation_level=None: no implicit transactions; transaction() opens them
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL, far fewer fsyncs
        self._conn.execute("PRAGMA busy_timeout=5000")  # another process (the CLI) may write
        self._migrate()
        self._conn.execute("PRAGMA foreign_keys=ON")

    def _migrate(self) -> None:
        """Run pending migrations, each in one transaction with its user_version
        bump: a step that fails leaves the database as it was, not half-built."""
        with self._lock:
            have = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, step in enumerate(MIGRATIONS[have:], start=have + 1):
                try:
                    self._conn.executescript(
                        f"BEGIN;\n{step()}\nPRAGMA user_version = {i};\nCOMMIT;")
                except sqlite3.Error:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes: all of them land, or none (nested calls join the outer one)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            self._depth -= 1
            if outer:
                self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, tuple(params))

    def prune(self, jobs_days: int = 30, http_days: int = 60) -> dict[str, int]:
        """Drop finished jobs and catalogue replies past their use, then let SQLite
        refresh its query statistics."""
        jobs_cut = (_dt.datetime.now() - _dt.timedelta(days=jobs_days)).isoformat(
            timespec="seconds")
        with self._lock:
            jobs = self._conn.execute(
                "DELETE FROM jobs WHERE status IN ('done','failed','cancelled')"
                " AND updated_at < ?", (jobs_cut,)).rowcount
            http = self._conn.execute("DELETE FROM http_cache WHERE fetched_at < ?",
                                      (time.time() - http_days * 86400,)).rowcount
            self._conn.execute("PRAGMA optimize")
        return {"jobs": jobs, "http_cache": http}

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

"""Content-addressed cache for rendered cuts.

The cache key is a hash of everything that determines the output bytes: the
ordered segments (with each source file's mtime and size, so a replaced episode
invalidates the entry), the resolved render mode, the chapters and the ffmpeg
encoding settings. The output file is named after the hash, so a repeat render
of an unchanged cut is a lookup, not an ffmpeg run.

Rows whose files disappear are dropped on sight; rows with keep=0 are deleted
by the sweep once they are older than `render.ephemeral_days`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from typing import Any

from ..config import FFmpegCfg
from ..db import Database
from ..planner import CutPlan


def render_hash(series_key: str, cut: CutPlan, mode: str, ff: FFmpegCfg,
                chapters: list[dict[str, Any]]) -> str:
    files: dict[str, list[Any] | None] = {}
    for s in cut.segments:
        if s.file in files:
            continue
        try:
            st = os.stat(s.file)
            files[s.file] = [st.st_mtime, st.st_size]
        except OSError:
            files[s.file] = None
    payload = {
        "v": 1,
        "series": series_key,
        "mode": mode,
        "ff": {"nvenc": ff.nvenc, "crf": ff.crf, "preset": ff.preset,
               "audio_bitrate": ff.audio_bitrate, "video_codec": ff.video_codec},
        # edit operations and music bridges shape the bytes too (only listed when present,
        # so plain segments hash as before)
        "segments": [[s.episode_id, s.file, s.start, s.end, s.xfade_ms]
                     + ([s.fx, s.audio_file] if s.fx or s.audio_file else [])
                     for s in cut.segments],
        "files": files,
        "chapters": chapters,
        "options": {**cut.options.__dict__, "trim_kinds": list(cut.options.trim_kinds)},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class RenderStore:
    """The renders table: one row per cached output file."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["keep"] = bool(d["keep"])
        d["file_exists"] = os.path.isfile(d["path"])
        return d

    def get(self, h: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM renders WHERE hash = ?", (h,))
        return self._row(row) if row else None

    def hit(self, h: str) -> dict[str, Any] | None:
        """The row only if its file is still on disk; stale rows are dropped."""
        r = self.get(h)
        if r is None:
            return None
        if not r["file_exists"]:
            self.remove(h)
            return None
        return r

    def add(self, h: str, series_key: str, title: str, path: str, mode: str,
            keep: bool, job_id: str = "") -> dict[str, Any]:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        self.db.execute(
            "INSERT OR REPLACE INTO renders"
            " (hash, series_key, title, path, mode, keep, size_bytes, job_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (h, series_key, title, path, mode, int(keep), size, job_id, _now()))
        return self.get(h)  # type: ignore[return-value]

    def set_keep(self, h: str, keep: bool) -> dict[str, Any] | None:
        self.db.execute("UPDATE renders SET keep = ? WHERE hash = ?", (int(keep), h))
        return self.get(h)

    def remove(self, h: str) -> dict[str, Any] | None:
        r = self.get(h)
        self.db.execute("DELETE FROM renders WHERE hash = ?", (h,))
        return r

    def drop_path(self, path: str, except_hash: str = "") -> None:
        """Forget rows pointing at a path another render just overwrote (file kept)."""
        self.db.execute("DELETE FROM renders WHERE path = ? AND hash != ?", (path, except_hash))

    def all(self, series_key: str | None = None) -> list[dict[str, Any]]:
        if series_key:
            rows = self.db.query("SELECT * FROM renders WHERE series_key = ?"
                                 " ORDER BY created_at DESC", (series_key,))
        else:
            rows = self.db.query("SELECT * FROM renders ORDER BY created_at DESC")
        out = []
        for r in rows:
            d = self._row(r)
            if d["file_exists"]:
                out.append(d)
            else:
                self.remove(d["hash"])
        return out

    def due(self, cutoff_iso: str) -> list[dict[str, Any]]:
        """Non-kept renders created before the cutoff."""
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM renders WHERE keep = 0 AND created_at < ?", (cutoff_iso,))]

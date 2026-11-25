"""SQLite cache for per-episode audio fingerprints.

Decoding five minutes of audio per episode is the slow part of OP/ED
detection, and the result depends only on the file (path + mtime + size) and
the fingerprint settings. Both go into the key, so replacing a file or
changing the fingerprint config invalidates the entry on its own.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np

from ..config import FingerprintCfg
from ..db import Database
from .audio import BANDS
from .detect import EpisodeClips


def cache_key(path: str, fp: FingerprintCfg) -> str | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    raw = f"{path}|{st.st_mtime}|{st.st_size}|{fp.sample_rate}|{fp.head_minutes}|{fp.tail_minutes}"
    return hashlib.sha256(raw.encode()).hexdigest()


class FingerprintCache:
    def __init__(self, db: Database):
        self.db = db

    def get(self, path: str, fp: FingerprintCfg) -> EpisodeClips | None:
        key = cache_key(path, fp)
        if key is None:
            return None
        row = self.db.one("SELECT * FROM fingerprint_cache WHERE key = ?", (key,))
        if row is None:
            return None
        head = np.frombuffer(row["head"], dtype=np.float32).reshape(row["head_rows"], BANDS)
        tail = np.frombuffer(row["tail"], dtype=np.float32).reshape(row["tail_rows"], BANDS)
        return EpisodeClips(head.copy(), tail.copy(), row["tail_start"], row["duration"])

    def put(self, path: str, clips: EpisodeClips, fp: FingerprintCfg) -> None:
        key = cache_key(path, fp)
        if key is None:
            return
        self.db.execute(
            "INSERT OR REPLACE INTO fingerprint_cache"
            " (key, head, head_rows, tail, tail_rows, tail_start, duration)"
            " VALUES (?,?,?,?,?,?,?)",
            (key, clips.head.tobytes(), clips.head.shape[0],
             clips.tail.tobytes(), clips.tail.shape[0], clips.tail_start, clips.duration))

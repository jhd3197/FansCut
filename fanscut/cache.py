"""An in-memory cache for source listings: stale-while-revalidate, single-flight,
per-prefix TTLs, a remembered failure, and an LRU cap.

- A fresh entry is returned as is.
- A stale entry (older than its TTL) is returned at once while one background
  thread refreshes it, so a page never waits on Sonarr just because 30 s passed.
- An entry older than `max_stale` is refetched in the foreground.
- A failed load is remembered for `fail_ttl` seconds: a down server costs one
  timeout, not one per request. If an older value exists it is served instead.
- Concurrent callers for the same key share one load.
- At most `max_entries` keys are kept; the least recently used go first.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("fanscut.cache")


@dataclass
class _Entry:
    value: Any = None
    stamp: float = 0.0  # when value was loaded; 0 = never
    error: BaseException | None = None
    error_stamp: float = 0.0
    refreshing: bool = False


@dataclass
class Stats:
    hits: int = 0
    stale: int = 0
    misses: int = 0
    failures: int = 0
    by_prefix: dict[str, int] = field(default_factory=dict)


class SourceCache:
    def __init__(self, ttls: dict[str, float], default_ttl: float = 300.0,
                 max_stale: float = 86400.0, fail_ttl: float = 45.0, max_entries: int = 500,
                 background: bool = True):
        self.ttls = ttls
        self.default_ttl = default_ttl
        self.max_stale = max_stale
        self.fail_ttl = fail_ttl
        self.max_entries = max_entries
        self.background = background
        self._data: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, Future[Any]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fanscut-cache")
        self.stats = Stats()

    def ttl_of(self, key: str) -> float:
        prefix = key.split(":", 1)[0]
        return self.ttls.get(prefix, self.default_ttl)

    def get(self, key: str, loader: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            e = self._data.get(key)
            if e is not None:
                self._data.move_to_end(key)
                age = now - e.stamp
                if e.stamp and age < self.ttl_of(key):
                    self.stats.hits += 1
                    return e.value
                if e.error is not None and now - e.error_stamp < self.fail_ttl:
                    if e.stamp:  # a recent failure, but an older value: serve that
                        self.stats.stale += 1
                        return e.value
                    self.stats.failures += 1
                    raise e.error
                if e.stamp and age < self.max_stale and self.background:
                    self.stats.stale += 1
                    if not e.refreshing:
                        e.refreshing = True
                        self._pool.submit(self._refresh, key, loader)
                    return e.value
        return self._load(key, loader)

    def _load(self, key: str, loader: Callable[[], Any]) -> Any:
        """Load in the foreground, sharing the work with concurrent callers."""
        with self._lock:
            fut = self._inflight.get(key)
            owner = fut is None
            if owner:
                fut = self._inflight[key] = Future()
                self.stats.misses += 1
        assert fut is not None
        if not owner:
            return fut.result()
        try:
            value = loader()
        except BaseException as exc:
            with self._lock:
                e = self._entry(key)
                e.error, e.error_stamp = exc, time.monotonic()
                self.stats.failures += 1
                del self._inflight[key]
                stale = e.value if e.stamp else None
                had = bool(e.stamp)
            fut.set_exception(exc)
            fut.exception()  # mark retrieved
            if had:
                return stale
            raise
        with self._lock:
            e = self._entry(key)
            e.value, e.stamp, e.error = value, time.monotonic(), None
            del self._inflight[key]
        fut.set_result(value)
        return value

    def _refresh(self, key: str, loader: Callable[[], Any]) -> None:
        try:
            value = loader()
        except Exception as exc:  # noqa: BLE001 - keep serving the old value
            log.info("background refresh of %s failed: %s", key, exc)
            with self._lock:
                e = self._entry(key)
                e.error, e.error_stamp, e.refreshing = exc, time.monotonic(), False
            return
        with self._lock:
            e = self._entry(key)
            e.value, e.stamp, e.error, e.refreshing = value, time.monotonic(), None, False

    def reload(self, key: str, loader: Callable[[], Any]) -> None:
        """Load `key` now and swap it in; readers keep the old value meanwhile."""
        self._refresh(key, loader)

    def peek(self, key: str) -> Any:
        """The last loaded value, however old, without loading; None if never loaded."""
        with self._lock:
            e = self._data.get(key)
            return e.value if e is not None and e.stamp else None

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data)

    def _entry(self, key: str) -> _Entry:
        e = self._data.get(key)
        if e is None:
            e = self._data[key] = _Entry()
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
        return e

    def clear(self, prefix: str | None = None) -> None:
        with self._lock:
            if prefix is None:
                self._data.clear()
            else:
                for k in [k for k in self._data if k.startswith(prefix)]:
                    del self._data[k]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            ages = [now - e.stamp for e in self._data.values() if e.stamp]
            prefixes: dict[str, int] = {}
            for k in self._data:
                p = k.split(":", 1)[0]
                prefixes[p] = prefixes.get(p, 0) + 1
            return {"entries": len(self._data), "by_prefix": prefixes,
                    "oldest_seconds": round(max(ages)) if ages else None,
                    "hits": self.stats.hits, "stale": self.stats.stale,
                    "misses": self.stats.misses, "failures": self.stats.failures}

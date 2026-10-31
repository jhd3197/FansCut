"""Rate-limited, SQLite-cached JSON fetching for the catalogues.

Offline-safe: a network failure serves a stale cache entry if there is one,
otherwise raises ClientError; callers treat that as "no data", never a crash.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from ..db import Database
from ..errors import ClientError


class RateLimiter:
    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.min_interval = min_interval
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._next:
                self._sleep(self._next - now)
                now = self._next
            self._next = now + self.min_interval


class CachedHttp:
    def __init__(self, db: Database, min_interval: float, ttl_days: int = 14,
                 offline: bool = False, session: requests.Session | None = None,
                 limiter: RateLimiter | None = None):
        self.db = db
        self.ttl = ttl_days * 86400
        self.offline = offline
        self.session = session or requests.Session()
        self.limiter = limiter or RateLimiter(min_interval)

    @staticmethod
    def _key(method: str, url: str, params: Any) -> str:
        blob = json.dumps([method, url, params], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def request(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                body: dict[str, Any] | None = None) -> Any:
        key = self._key(method, url, [params, body])
        row = self.db.one("SELECT body, fetched_at FROM http_cache WHERE key = ?", (key,))
        fresh = row is not None and time.time() - row["fetched_at"] < self.ttl
        if row and (fresh or self.offline):
            return json.loads(row["body"])
        if self.offline:
            raise ClientError("offline mode and nothing cached")
        try:
            data = self._fetch(method, url, params, body)
        except ClientError:
            if row:  # stale beats nothing
                return json.loads(row["body"])
            raise
        self.db.execute(
            "INSERT OR REPLACE INTO http_cache (key, body, fetched_at) VALUES (?,?,?)",
            (key, json.dumps(data), time.time()),
        )
        return data

    def _fetch(self, method: str, url: str, params: Any, body: Any) -> Any:
        for attempt in range(3):
            self.limiter.wait()
            try:
                resp = self.session.request(method, url, params=params, json=body, timeout=20)
            except requests.RequestException as exc:
                raise ClientError(f"{url} unreachable: {exc.__class__.__name__}") from exc
            if resp.status_code == 429 and attempt < 2:
                time.sleep(min(float(resp.headers.get("Retry-After", 2) or 2), 10))
                continue
            if resp.status_code >= 400:
                raise ClientError(f"{url} returned HTTP {resp.status_code}", resp.status_code)
            try:
                return resp.json()
            except ValueError as exc:
                raise ClientError(f"{url} sent a non-JSON reply") from exc
        raise ClientError(f"{url} kept rate-limiting us", 429)  # pragma: no cover

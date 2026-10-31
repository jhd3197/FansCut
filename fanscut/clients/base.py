"""Shared HTTP base for the Sonarr/Plex clients: session, timeout, retry on transient errors."""

from __future__ import annotations

import time
from typing import Any

import requests

from ..errors import ClientError


class BaseClient:
    service = "service"
    max_retries = 2
    backoff = 0.5
    max_wait = 30.0

    def __init__(self, base_url: str, timeout: float = 30, headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)

    def _once(self, path: str, params: dict[str, Any] | None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClientError(f"{self.service} unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (401, 403):
            raise ClientError(f"{self.service} rejected the credentials", resp.status_code)
        if resp.status_code >= 400:
            raise ClientError(
                f"{self.service} returned HTTP {resp.status_code}",
                resp.status_code,
                resp.headers.get("Retry-After"),
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise ClientError(f"{self.service} sent a non-JSON reply", resp.status_code) from exc

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        for attempt in range(self.max_retries + 1):
            try:
                return self._once(path, params)
            except ClientError as exc:
                transient = exc.status is None or exc.status == 429 or exc.status >= 500
                if not transient or attempt == self.max_retries:
                    raise
                delay = self.backoff * 2**attempt
                if exc.retry_after and exc.retry_after.isdigit():
                    delay = max(delay, float(exc.retry_after))
                time.sleep(min(delay, self.max_wait))
        raise AssertionError("unreachable")  # pragma: no cover

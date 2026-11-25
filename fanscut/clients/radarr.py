"""Radarr v3 API: movies with files (read-only)."""

from __future__ import annotations

from typing import Any

from .base import BaseClient


class RadarrClient(BaseClient):
    service = "Radarr"

    def __init__(self, base_url: str, api_key: str, timeout: float = 30):
        super().__init__(base_url, timeout, {"X-Api-Key": api_key})

    def system_status(self) -> dict[str, Any]:
        return self._get("/api/v3/system/status")

    def list_movies(self) -> list[dict[str, Any]]:
        return self._get("/api/v3/movie")

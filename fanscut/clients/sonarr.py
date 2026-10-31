"""Sonarr v3 API: series, episodes and episode files (read-only)."""

from __future__ import annotations

from typing import Any

from .base import BaseClient


class SonarrClient(BaseClient):
    service = "Sonarr"

    def __init__(self, base_url: str, api_key: str, timeout: float = 30):
        super().__init__(base_url, timeout, {"X-Api-Key": api_key})

    def system_status(self) -> dict[str, Any]:
        return self._get("/api/v3/system/status")

    def list_series(self) -> list[dict[str, Any]]:
        return self._get("/api/v3/series")

    def episodes(self, series_id: int) -> list[dict[str, Any]]:
        return self._get("/api/v3/episode", {"seriesId": series_id})

    def episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return self._get("/api/v3/episodefile", {"seriesId": series_id})

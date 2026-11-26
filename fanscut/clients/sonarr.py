"""Sonarr v3 API: series, episodes and episode files (read-only)."""

from __future__ import annotations

from typing import Any

from .base import ArrClient


class SonarrClient(ArrClient):
    service = "Sonarr"

    def cover(self, series_id: str, kind: str = "poster") -> bytes | None:
        """Sonarr's cached poster (500 px wide) or fanart (backdrop)."""
        name = "poster-500.jpg" if kind == "poster" else "fanart.jpg"
        return self.get_image(f"/api/v3/MediaCover/{series_id}/{name}")

    def list_series(self) -> list[dict[str, Any]]:
        return self._get("/api/v3/series")

    def episodes(self, series_id: int) -> list[dict[str, Any]]:
        return self._get("/api/v3/episode", {"seriesId": series_id})

    def episode_files(self, series_id: int) -> list[dict[str, Any]]:
        return self._get("/api/v3/episodefile", {"seriesId": series_id})

"""Radarr v3 API: movies with files (read-only)."""

from __future__ import annotations

from typing import Any

from .base import ArrClient


class RadarrClient(ArrClient):
    service = "Radarr"

    def list_movies(self) -> list[dict[str, Any]]:
        return self._get("/api/v3/movie")

    def cover(self, movie_id: str, kind: str = "poster") -> bytes | None:
        """Radarr's cached poster (500 px wide) or fanart (backdrop)."""
        name = "poster-500.jpg" if kind == "poster" else "fanart.jpg"
        return self.get_image(f"/api/v3/MediaCover/{movie_id}/{name}")

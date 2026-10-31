"""Plex API: library sections, shows and episodes with file paths (read-only).

The token goes in a header so it stays out of URLs and logs.
"""

from __future__ import annotations

from typing import Any

from .base import BaseClient


class PlexClient(BaseClient):
    service = "Plex"

    def __init__(self, base_url: str, token: str, timeout: float = 30):
        super().__init__(
            base_url, timeout, {"X-Plex-Token": token, "Accept": "application/json"}
        )

    def identity(self) -> dict[str, Any]:
        return self._get("/")["MediaContainer"]

    def sections(self) -> list[dict[str, Any]]:
        data = self._get("/library/sections")["MediaContainer"].get("Directory", [])
        return [{"key": str(s["key"]), "type": s["type"], "title": s["title"]} for s in data]

    def shows(self, section_key: str) -> list[dict[str, Any]]:
        return self._get(f"/library/sections/{section_key}/all", {"type": 2})["MediaContainer"].get(
            "Metadata", []
        )

    def episodes(self, show_rating_key: str) -> list[dict[str, Any]]:
        return self._get(f"/library/metadata/{show_rating_key}/allLeaves")["MediaContainer"].get(
            "Metadata", []
        )

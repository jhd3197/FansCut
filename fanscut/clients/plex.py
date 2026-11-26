"""Plex API: library sections, shows and episodes with file paths, library refresh.

The token goes in a header so it stays out of URLs and logs.
"""

from __future__ import annotations

from typing import Any

import requests

from ..errors import ClientError
from .base import BaseClient


class PlexClient(BaseClient):
    service = "Plex"

    def __init__(self, base_url: str, token: str, timeout: float = 30):
        super().__init__(
            base_url, timeout, {"X-Plex-Token": token, "Accept": "application/json"}
        )

    def identity(self) -> dict[str, Any]:
        return self._get("/")["MediaContainer"]

    def clean_art_path(self, rating_key: str, kind: str = "poster") -> str | None:
        """The item's own agent art (TMDB/TVDB), skipping uploads: tools like Kometa
        upload a copy with ratings and badges drawn on, and select it. None = no list."""
        try:
            which = "posters" if kind == "poster" else "arts"
            data = self._get(f"/library/metadata/{rating_key}/{which}")
        except ClientError:
            return None
        for p in (data.get("MediaContainer") or {}).get("Metadata", []):
            key = str(p.get("key", ""))
            if key and p.get("provider") not in ("upload", "local") and "upload" not in key:
                return key
        return None

    def artwork(self, rating_key: str, kind: str = "poster", width: int = 500,
                path: str | None = None) -> bytes | None:
        """An item's poster (thumb) or backdrop (art), resized by Plex's photo transcoder.
        `path` is the thumb/art path from the listing, when known."""
        field = "thumb" if kind == "poster" else "art"
        height = width * 3 // 2 if kind == "poster" else width * 9 // 16
        return self.get_image("/photo/:/transcode", {
            "url": path or f"/library/metadata/{rating_key}/{field}", "width": width,
            "height": height,
            "minSize": 1, "upscale": 1})

    def sections(self) -> list[dict[str, Any]]:
        data = self._get("/library/sections")["MediaContainer"].get("Directory", [])
        return [{"key": str(s["key"]), "type": s["type"], "title": s["title"]} for s in data]

    def shows(self, section_key: str) -> list[dict[str, Any]]:
        return self._get(f"/library/sections/{section_key}/all",
                         {"type": 2, "includeGuids": 1})["MediaContainer"].get(
            "Metadata", []
        )

    def episodes(self, show_rating_key: str) -> list[dict[str, Any]]:
        return self._get(f"/library/metadata/{show_rating_key}/allLeaves")["MediaContainer"].get(
            "Metadata", []
        )

    def movies(self, section_key: str) -> list[dict[str, Any]]:
        return self._get(f"/library/sections/{section_key}/all",
                         {"type": 1, "includeGuids": 1})["MediaContainer"].get(
            "Metadata", []
        )

    def refresh_section(self, key: str) -> None:
        """Ask Plex to rescan a library section (best effort, no reply body)."""
        try:
            resp = self.session.put(f"{self.base_url}/library/sections/{key}/refresh",
                                    timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClientError(f"Plex unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ClientError(f"Plex returned HTTP {resp.status_code}", resp.status_code)

    def find_in_section(self, section_key: str, title: str) -> dict[str, Any] | None:
        """The item in a section with exactly this title (Plex's filter also matches
        titles that merely contain it, so the reply is checked)."""
        data = self._get(f"/library/sections/{section_key}/all", {"title": title})
        items = data["MediaContainer"].get("Metadata", [])
        want = title.casefold()
        return next((i for i in items if str(i.get("title", "")).casefold() == want), None)

    def _item_uri(self, rating_key: str) -> str:
        machine = self.identity()["machineIdentifier"]
        # requests urlencodes params once; do not pre-quote
        return f"library://{machine}/item//library/metadata/{rating_key}"

    def create_playlist(self, title: str, rating_keys: list[str]) -> str:
        """A video playlist of the given items, in order. Returns the playlist id."""
        if not rating_keys:
            raise ClientError("a playlist needs at least one item")
        created = self._request("POST", "/playlists", {
            "type": "video", "title": title, "smart": 0,
            "uri": self._item_uri(rating_keys[0])})
        pid = str(created["MediaContainer"]["Metadata"][0]["ratingKey"])
        for key in rating_keys[1:]:
            self._request("PUT", f"/playlists/{pid}/items", {"uri": self._item_uri(key)})
        return pid

    def collection_id(self, section_key: str, title: str,
                      first_key: str | None = None) -> str:
        """The collection's id, creating it if needed."""
        data = self._get(f"/library/sections/{section_key}/collections", {"title": title})
        items = data["MediaContainer"].get("Metadata", [])
        if items:
            return str(items[0]["ratingKey"])
        if not first_key:
            raise ClientError(f"cannot create collection {title!r} without an item")
        created = self._request("PUT", "/library/collections", {
            "type": 18, "title": title, "smart": 0, "sectionId": section_key,
            "uri": self._item_uri(first_key)})
        return str(created["MediaContainer"]["Metadata"][0]["ratingKey"])

    def add_to_collection(self, collection_id: str, rating_key: str) -> None:
        self._request("PUT", f"/library/collections/{collection_id}/items",
                      {"uri": self._item_uri(rating_key)})

    def _request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        try:
            resp = self.session.request(method, f"{self.base_url}{path}", params=params,
                                        timeout=self.timeout)
        except requests.RequestException as exc:
            raise ClientError(f"Plex unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise ClientError(f"Plex returned HTTP {resp.status_code}", resp.status_code)
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise ClientError("Plex sent a non-JSON reply", resp.status_code) from exc

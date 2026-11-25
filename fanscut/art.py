"""Posters and backdrops for library items, from whichever source has them.

An item can be listed by several sources (Radarr and Plex, say). Its art is
looked up across every copy of it: Plex first (it resizes on request), then
Radarr or Sonarr's cached covers, then image files next to the media in the
naming Plex and Jellyfin use (poster.jpg, folder.jpg, fanart.jpg, ...).

Results are cached under <data_dir>/art; a miss is remembered for a day so a
library without art does not ask every server again on every page view.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .library.service import Library

KINDS = ("poster", "backdrop")
HIT_TTL = 7 * 86400
MISS_TTL = 86400

POSTER_FILES = ("poster", "folder", "cover", "movie", "show")
BACKDROP_FILES = ("fanart", "backdrop", "background", "art")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def media_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _width(w: int) -> int:
    """Round to 100 px steps so the cache holds a handful of sizes, not every one."""
    return max(100, min(1200, (w + 99) // 100 * 100))


def local_image(folder: Path, kind: str, stem: str = "") -> Path | None:
    """poster.jpg / folder.jpg / <file>-poster.jpg (fanart.jpg ... for backdrops) in a folder."""
    names = POSTER_FILES if kind == "poster" else BACKDROP_FILES
    wanted = [n for n in names] + ([f"{stem}-{names[0]}"] if stem else [])
    try:
        files = {p.stem.lower(): p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower() in IMAGE_EXTS}
    except OSError:
        return None
    for name in wanted:
        if name.lower() in files:
            return files[name.lower()]
    return None


def art_ref(item: Any, kind: str = "poster") -> str:
    """What the UI loads for an item's art: a direct https URL when Radarr or
    Sonarr gave one (TMDB's CDN, no round trip through FansCut), else the
    item's key, which the UI turns into /api/art/<kind>/<key>."""
    url = getattr(item, "poster" if kind == "poster" else "backdrop", None)
    return url if url and url.startswith("https://") else item.key


class ArtStore:
    def __init__(self, library: Library, cache_dir: Path):
        self.library = library
        self.cache_dir = cache_dir

    # -- cache ------------------------------------------------------------------
    def _path(self, key: str, kind: str, width: int) -> Path:
        # v2: Plex art skips uploaded (overlaid) posters; older cache entries are not reused
        digest = hashlib.sha1(f"v2|{key}|{kind}|{width}".encode()).hexdigest()
        return self.cache_dir / digest

    def get(self, key: str, kind: str = "poster", width: int = 500) -> bytes | None:
        if kind not in KINDS:
            raise ValueError(f"unknown art kind {kind!r}")
        width = _width(width)
        hit, miss = self._path(key, kind, width), self._path(key, kind, width).with_suffix(".none")
        now = time.time()
        if hit.exists() and now - hit.stat().st_mtime < HIT_TTL:
            return hit.read_bytes()
        if miss.exists() and now - miss.stat().st_mtime < MISS_TTL:
            return None
        data = self.fetch(key, kind, width)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if data:
            tmp = hit.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, hit)
            miss.unlink(missing_ok=True)
        else:
            miss.touch()
        return data

    # -- lookup -----------------------------------------------------------------
    def _copies(self, key: str) -> list[Any]:
        """Every source's entry for the same title as `key` (itself first)."""
        movies, _ = self.library.list_movies(merged=False)
        movie = next((m for m in movies if m.key == key), None)
        if movie is not None:
            norm = os.path.normcase
            return [movie, *(m for m in movies if m is not movie and (
                (movie.tmdb_id and m.tmdb_id == movie.tmdb_id)
                or (movie.file and m.file and norm(m.file) == norm(movie.file))))]
        series, _ = self.library.list_series(merged=False)
        show = next((s for s in series if s.key == key), None)
        if show is None:
            return []
        return [show, *(s for s in series if s is not show and (
            (show.tvdb_id and s.tvdb_id == show.tvdb_id)
            or (show.tmdb_id and s.tmdb_id == show.tmdb_id)
            or (s.title.lower() == show.title.lower() and s.year == show.year)))]

    def fetch(self, key: str, kind: str, width: int) -> bytes | None:
        copies = self._copies(key)
        order = {"plexmovie": 0, "plex": 0, "radarr": 1, "sonarr": 1}
        copies.sort(key=lambda c: order.get(c.key.split(":", 1)[0], 2))
        for c in copies:
            data = self._from_copy(c, kind, width)
            if data:
                return data
        return None

    def _from_copy(self, item: Any, kind: str, width: int) -> bytes | None:
        src, ident = item.key.split(":", 1)
        if src in ("plexmovie", "plex"):
            plex = self.library.plex()
            if plex is None:
                return None
            # the clean agent art first; the selected one may carry Kometa overlays
            path = plex.clean_art_path(ident, kind) or getattr(
                item, "poster" if kind == "poster" else "backdrop", None)
            return plex.artwork(ident, kind, width, path)
        if src == "radarr":
            radarr = self.library.radarr()
            return radarr.cover(ident, kind) if radarr else None
        if src == "sonarr":
            sonarr = self.library.sonarr()
            return sonarr.cover(ident, kind) if sonarr else None
        found = None
        if getattr(item, "file", None):  # a movie: art sits beside its file
            f = Path(item.file)
            found = local_image(f.parent, kind, f.stem)
        elif src == "folder":  # a folder-mode show: art sits in the show's folder
            root = self.library.cfg.library.folder.path
            found = local_image(Path(root) / ident, kind) if root else None
        return found.read_bytes() if found else None

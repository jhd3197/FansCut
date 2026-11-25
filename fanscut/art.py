"""Posters and backdrops for library items, from whichever source has them.

An item can be listed by several sources (Radarr and Plex, say). Its art is
looked up across every copy of it: Plex first (it resizes on request), then
Radarr or Sonarr's cached covers, then image files next to the media in the
naming Plex and Jellyfin use (poster.jpg, folder.jpg, fanart.jpg, ...).

Results are cached under <data_dir>/art:

- fresh for HIT_TTL; after that the old image is still served while one
  background fetch refreshes it, and kept if that fetch fails;
- a real "no art" (404, not an image) is remembered for MISS_TTL; a network
  error or a 5xx is not remembered at all, so the next view tries again;
- one fetch per image at a time, at most MAX_FETCHES at once (a grid of 100
  tiles must not start 100 Plex transcodes);
- the folder is capped at art.cache_mb; the oldest files go first.

With art.cache_remote on, TMDB and TVDB images that Radarr/Sonarr point at are
fetched and cached here too, so browsers never talk to those CDNs.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

import requests

from .errors import ClientError
from .net import read_capped

if TYPE_CHECKING:
    from .library.service import Library

log = logging.getLogger("fanscut.art")

KINDS = ("poster", "backdrop")
HIT_TTL = 7 * 86400
MISS_TTL = 86400
STALE_RETRY = 3600  # a failed background refresh is tried again after this
# Bump when the way art is chosen or sized changes: old entries stop matching.
CACHE_VERSION = 3
MAX_FETCHES = 4
MAX_IMAGE = 20_000_000
REMOTE_HOSTS = {"image.tmdb.org", "artworks.thetvdb.com"}
TMDB_SIZES = {"poster": (185, 342, 500, 780), "backdrop": (300, 780, 1280)}

POSTER_FILES = ("poster", "folder", "cover", "movie", "show")
BACKDROP_FILES = ("fanart", "backdrop", "background", "art")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")

# Set from art.cache_remote by Core.apply_config: art_ref then hands out
# "remote:<url>" keys (served from this cache) instead of CDN URLs.
CACHE_REMOTE = False


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


def remote_ok(url: str) -> bool:
    """Only https URLs on the image CDNs may be fetched for a remote: key (no SSRF)."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname in REMOTE_HOSTS


def art_ref(item: Any, kind: str = "poster") -> str:
    """What the UI loads for an item's art.

    A direct https URL when Radarr or Sonarr gave one (TMDB's CDN, no round trip
    through FansCut). Its fragment carries the item's key, "#k=<key>", which the
    browser ignores when loading but the UI uses to fall back to
    /api/art/<kind>/<key> (Plex or files) if the CDN fails. Without a URL, or
    with art.cache_remote on, a key, which the UI turns into /api/art/<kind>/<key>."""
    url = getattr(item, "poster" if kind == "poster" else "backdrop", None)
    if not (url and url.startswith("https://")):
        return str(item.key)
    if CACHE_REMOTE and remote_ok(url):
        return f"remote:{url}"
    return f"{url}#k={quote(str(item.key), safe='')}"


@dataclass
class Art:
    data: bytes
    status: str  # HIT, STALE or MISS (fetched just now)
    max_age: int  # seconds the browser may keep it
    etag: str


class ArtStore:
    def __init__(self, library: Library, cache_dir: Path, max_bytes: int = 1 << 30,
                 session: requests.Session | None = None):
        self.library = library
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.session = session or requests.Session()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._fetching = threading.BoundedSemaphore(MAX_FETCHES)
        self._refreshing: set[str] = set()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fanscut-art")
        self._writes = 0
        self.counts = {"hit": 0, "stale": 0, "miss": 0, "none": 0, "error": 0}

    # -- cache ------------------------------------------------------------------
    def _path(self, key: str, kind: str, width: int) -> Path:
        digest = hashlib.sha1(f"v{CACHE_VERSION}|{key}|{kind}|{width}".encode()).hexdigest()
        return self.cache_dir / digest

    def _lock(self, name: str) -> threading.Lock:
        with self._locks_guard:
            lk = self._locks.get(name)
            if lk is None:
                if len(self._locks) > 2000:
                    self._locks = {k: v for k, v in self._locks.items() if v.locked()}
                lk = self._locks[name] = threading.Lock()
            return lk

    def get(self, key: str, kind: str = "poster", width: int = 500) -> bytes | None:
        art = self.lookup(key, kind, width)
        return art.data if art else None

    def lookup(self, key: str, kind: str = "poster", width: int = 500) -> Art | None:
        if kind not in KINDS:
            raise ValueError(f"unknown art kind {kind!r}")
        width = _width(width)
        hit = self._path(key, kind, width)
        miss = hit.with_suffix(".none")
        art = self._cached(hit)
        if art is not None:
            if art.status == "STALE":
                self._refresh_later(hit, key, kind, width)
            return art
        if self._fresh_miss(miss):
            self.counts["none"] += 1
            return None
        with self._lock(hit.name):  # one fetch per image; later callers read its result
            art = self._cached(hit)
            if art is not None:
                return art
            if self._fresh_miss(miss):
                return None
            try:
                data = self._fetch_limited(key, kind, width)
            except ClientError as exc:  # transient: remember nothing, try again next time
                self.counts["error"] += 1
                log.debug("art for %s not fetched: %s", key, exc)
                return None
            self.counts["miss"] += 1
            if not data:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                miss.touch()
                return None
            self._store(hit, data)
            miss.unlink(missing_ok=True)
            return Art(data, "MISS", HIT_TTL, self._etag(hit.stat()))

    @staticmethod
    def _etag(st: os.stat_result) -> str:
        return f'"{st.st_mtime_ns:x}-{st.st_size:x}"'

    @staticmethod
    def _fresh_miss(miss: Path) -> bool:
        try:
            return time.time() - miss.stat().st_mtime < MISS_TTL
        except OSError:
            return False

    def _cached(self, hit: Path) -> Art | None:
        try:
            st = hit.stat()
            data = hit.read_bytes()
        except OSError:
            return None
        age = time.time() - st.st_mtime
        if age < HIT_TTL:
            self.counts["hit"] += 1
            return Art(data, "HIT", int(HIT_TTL - age), self._etag(st))
        self.counts["stale"] += 1
        return Art(data, "STALE", 300, self._etag(st))

    def _refresh_later(self, hit: Path, key: str, kind: str, width: int) -> None:
        with self._locks_guard:
            if hit.name in self._refreshing:
                return
            self._refreshing.add(hit.name)
        self._pool.submit(self._refresh, hit, key, kind, width)

    def _refresh(self, hit: Path, key: str, kind: str, width: int) -> None:
        try:
            with self._lock(hit.name):
                try:
                    data = self._fetch_limited(key, kind, width)
                except ClientError:
                    # keep the old image; try again in an hour, not on every view
                    retry = time.time() - HIT_TTL + STALE_RETRY
                    with contextlib.suppress(OSError):
                        os.utime(hit, (retry, retry))
                    return
                if data:
                    self._store(hit, data)
                else:  # the source dropped it: keep what we have for another round
                    with contextlib.suppress(OSError):
                        os.utime(hit)
        except Exception:  # noqa: BLE001 - a background refresh must never crash the pool
            log.exception("art refresh for %s failed", key)
        finally:
            with self._locks_guard:
                self._refreshing.discard(hit.name)

    def _fetch_limited(self, key: str, kind: str, width: int) -> bytes | None:
        with self._fetching:
            return self.fetch(key, kind, width)

    def _store(self, path: Path, data: bytes) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.cache_dir, prefix=".", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        self._writes += 1
        if self._writes % 50 == 0:
            self.evict()

    # -- housekeeping -------------------------------------------------------------
    def _files(self) -> list[tuple[Path, os.stat_result]]:
        out = []
        try:
            for p in self.cache_dir.iterdir():
                with contextlib.suppress(OSError):
                    out.append((p, p.stat()))
        except OSError:
            pass
        return out

    def evict(self) -> int:
        """Delete the oldest images until the folder is under 90% of max_bytes."""
        files = [(p, st) for p, st in self._files() if not p.name.endswith(".none")]
        total = sum(st.st_size for _, st in files)
        if total <= self.max_bytes:
            return 0
        removed = 0
        for p, st in sorted(files, key=lambda f: f[1].st_mtime):
            if total <= self.max_bytes * 0.9:
                break
            with contextlib.suppress(OSError):
                p.unlink()
                total -= st.st_size
                removed += 1
        return removed

    def prune(self) -> int:
        """Delete expired misses, leftovers of interrupted writes, and images not
        refreshed in a long time (nobody has looked at them)."""
        now, removed = time.time(), 0
        for p, st in self._files():
            age = now - st.st_mtime
            if (p.name.endswith(".none") and age > MISS_TTL) \
                    or (p.name.endswith(".tmp") and age > 3600) or age > 4 * HIT_TTL:
                with contextlib.suppress(OSError):
                    p.unlink()
                    removed += 1
        return removed + self.evict()

    def stats(self) -> dict[str, Any]:
        files = self._files()
        images = [st for p, st in files if not p.name.endswith((".none", ".tmp"))]
        return {"images": len(images), "bytes": sum(st.st_size for st in images),
                "misses": sum(1 for p, _ in files if p.name.endswith(".none")),
                "max_bytes": self.max_bytes, "counts": dict(self.counts)}

    def clear(self) -> int:
        removed = 0
        for p, _ in self._files():
            with contextlib.suppress(OSError):
                p.unlink()
                removed += 1
        return removed

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
        """The image from the first copy that has one. Raises ClientError when no
        copy had it and at least one failed for a transient reason (so the caller
        does not record a miss)."""
        if key.startswith("remote:"):
            return self._from_remote(key[len("remote:"):], kind, width)
        copies = self._copies(key)
        order = {"plexmovie": 0, "plex": 0, "radarr": 1, "sonarr": 1}
        copies.sort(key=lambda c: order.get(c.key.split(":", 1)[0], 2))
        failed: ClientError | None = None
        for c in copies:
            try:
                data = self._from_copy(c, kind, width)
            except ClientError as exc:
                failed = exc
                continue
            if data:
                return data
        if failed is not None:
            raise failed
        return None

    def _from_remote(self, url: str, kind: str, width: int) -> bytes | None:
        if not remote_ok(url):
            return None
        parts = urlsplit(url)
        if parts.hostname == "image.tmdb.org":
            segs = parts.path.split("/")  # ["", "t", "p", <size>, <file>]
            if len(segs) >= 5:
                size = next((s for s in TMDB_SIZES[kind] if s >= width * 0.95),
                            TMDB_SIZES[kind][-1])
                segs[3] = f"w{size}"
                url = f"https://image.tmdb.org{'/'.join(segs)}"
        try:
            resp = self.session.get(url, timeout=15, stream=True,
                                    headers={"Accept": "image/*", "User-Agent": "FansCut"})
        except requests.RequestException as exc:
            raise ClientError(f"{parts.hostname} unreachable") from exc
        with resp:
            if resp.status_code == 429 or resp.status_code >= 500:
                raise ClientError(f"{parts.hostname} returned HTTP {resp.status_code}",
                                  resp.status_code)
            if resp.status_code != 200 \
                    or not resp.headers.get("Content-Type", "").startswith("image/"):
                return None
            return read_capped(resp, MAX_IMAGE)

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

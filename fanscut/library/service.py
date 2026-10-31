"""One view over Sonarr, Plex and the media folder.

Series keys are "<source>:<id>", so the three can be enabled together.
Durations missing from the source come from ffprobe, cached in SQLite.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from ..clients.plex import PlexClient
from ..clients.sonarr import SonarrClient
from ..config import Config
from ..errors import ClientError, NotFoundError
from ..models import Episode, Series
from . import scan
from .paths import map_path

TTL = 30.0  # seconds a source listing is reused


def _hms(text: str | None) -> float | None:
    if not text:
        return None
    try:
        h, m, s = (float(x) for x in text.split(":"))
    except ValueError:
        return None
    return h * 3600 + m * 60 + s


class Library:
    def __init__(self, cfg: Config, prober: Callable[[str], dict[str, Any]] | None = None):
        self.cfg = cfg
        self.prober = prober
        self._cache: dict[str, tuple[float, Any]] = {}

    # -- plumbing -----------------------------------------------------------
    def _cached(self, key: str, fn: Callable[[], Any]) -> Any:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
        val = fn()
        self._cache[key] = (time.monotonic(), val)
        return val

    def refresh(self) -> None:
        self._cache.clear()

    def sonarr(self) -> SonarrClient | None:
        c = self.cfg.library.sonarr
        return SonarrClient(c.url, c.api_key) if c.url else None

    def plex(self) -> PlexClient | None:
        c = self.cfg.library.plex
        return PlexClient(c.url, c.token) if c.url else None

    def sources(self) -> list[str]:
        lib = self.cfg.library
        return [n for n, on in (("sonarr", lib.sonarr.url), ("plex", lib.plex.url),
                                ("folder", lib.folder.path)) if on]

    def _map(self, path: str) -> str:
        return map_path(path, self.cfg.library.path_mappings)

    # -- series ---------------------------------------------------------------
    def list_series(self) -> tuple[list[Series], dict[str, str]]:
        """All series plus {source: error} for sources that failed (others still list)."""
        out: list[Series] = []
        errors: dict[str, str] = {}
        for src in self.sources():
            try:
                out.extend(self._cached(f"series:{src}", partial(self._series_of, src)))
            except ClientError as exc:
                errors[src] = str(exc)
        return sorted(out, key=lambda s: s.title.lower()), errors

    def _series_of(self, src: str) -> list[Series]:
        if src == "folder":
            return scan.list_folder_series(Path(self.cfg.library.folder.path))
        if src == "sonarr":
            sonarr = self.sonarr()
            assert sonarr is not None
            return [self._sonarr_series(s) for s in sonarr.list_series()]
        plex = self.plex()
        assert plex is not None
        want = set(self.cfg.library.plex.sections)
        found: list[Series] = []
        for sec in plex.sections():
            listed = not want or sec["key"] in want or sec["title"] in want
            if sec["type"] != "show" or not listed:
                continue
            found.extend(self._plex_series(m) for m in plex.shows(sec["key"]))
        return found

    @staticmethod
    def _sonarr_series(s: dict[str, Any]) -> Series:
        stats = s.get("statistics") or {}
        return Series(
            key=f"sonarr:{s['id']}", source="sonarr", title=s.get("title", ""),
            year=s.get("year") or None,
            alt_titles=[a.get("title", "") for a in s.get("alternateTitles", []) if a.get("title")],
            tvdb_id=s.get("tvdbId") or None, tmdb_id=s.get("tmdbId") or None,
            episode_count=stats.get("episodeFileCount", 0),
            is_anime=s.get("seriesType") == "anime",
        )

    @staticmethod
    def _plex_series(m: dict[str, Any]) -> Series:
        ids = {g["id"].split("://")[0]: g["id"].split("://")[1] for g in m.get("Guid", [])
               if "://" in g.get("id", "")}
        return Series(
            key=f"plex:{m['ratingKey']}", source="plex", title=m.get("title", ""),
            year=m.get("year"), episode_count=m.get("leafCount", 0),
            tvdb_id=int(ids["tvdb"]) if ids.get("tvdb", "").isdigit() else None,
            tmdb_id=int(ids["tmdb"]) if ids.get("tmdb", "").isdigit() else None,
        )

    def get_series(self, key: str) -> Series:
        series, _ = self.list_series()
        for s in series:
            if s.key == key:
                return s
        raise NotFoundError(f"series {key} not found")

    # -- episodes -------------------------------------------------------------
    def episodes(self, key: str) -> list[Episode]:
        src, _, ident = key.partition(":")
        if src not in ("sonarr", "plex", "folder") or not ident:
            raise NotFoundError(f"bad series key {key}")
        eps: list[Episode] = self._cached(f"eps:{key}", partial(self._episodes_of, src, ident))
        return eps

    def _episodes_of(self, src: str, ident: str) -> list[Episode]:
        if src == "folder":
            root = Path(self.cfg.library.folder.path) / ident
            base = Path(self.cfg.library.folder.path).resolve()
            if not root.is_dir() or root.resolve().parent != base:
                raise NotFoundError(f"series folder {ident} not found")
            eps = scan.scan_series_dir(root)
        elif src == "sonarr":
            eps = self._sonarr_episodes(ident)
        else:
            eps = self._plex_episodes(ident)
        for e in eps:
            if e.file and e.duration is None and self.prober and Path(e.file).is_file():
                try:
                    e.duration = self.prober(e.file).get("duration") or None
                except Exception:  # noqa: BLE001 - one unreadable file must not hide the series
                    e.duration = None
        return sorted(eps, key=lambda e: (e.season, e.number))

    def _sonarr_episodes(self, ident: str) -> list[Episode]:
        client = self.sonarr()
        if client is None or not ident.isdigit():
            raise NotFoundError("Sonarr is not configured")
        sid = int(ident)
        files = {f["id"]: f for f in client.episode_files(sid)}
        out = []
        for e in client.episodes(sid):
            f = files.get(e.get("episodeFileId"))
            runtime = _hms(((f or {}).get("mediaInfo") or {}).get("runTime"))
            out.append(Episode(
                season=e["seasonNumber"], number=e["episodeNumber"], title=e.get("title", ""),
                absolute=e.get("absoluteEpisodeNumber"),
                file=self._map(f["path"]) if f and f.get("path") else None,
                duration=runtime, air_date=e.get("airDate"),
            ))
        return out

    def _plex_episodes(self, ident: str) -> list[Episode]:
        client = self.plex()
        if client is None:
            raise NotFoundError("Plex is not configured")
        out = []
        for m in client.episodes(ident):
            media = (m.get("Media") or [{}])[0]
            part = (media.get("Part") or [{}])[0]
            ms = media.get("duration") or m.get("duration")
            out.append(Episode(
                season=m.get("parentIndex", 0), number=m.get("index", 0), title=m.get("title", ""),
                file=self._map(part["file"]) if part.get("file") else None,
                duration=ms / 1000 if ms else None, air_date=m.get("originallyAvailableAt"),
            ))
        return out

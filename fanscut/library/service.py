"""One view over Sonarr, Plex and the media folder.

Series keys are "<source>:<id>", so the three can be enabled together.
Durations missing from the source come from ffprobe, cached in SQLite.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from ..clients.plex import PlexClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..config import Config
from ..errors import ClientError, NotFoundError
from ..models import Episode, Movie, Series
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

    def radarr(self) -> RadarrClient | None:
        c = self.cfg.library.radarr
        return RadarrClient(c.url, c.api_key) if c.url else None

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
    def list_series(self, merged: bool = True) -> tuple[list[Series], dict[str, str]]:
        """All series plus {source: error} for sources that failed (others still list).

        With Sonarr and Plex both on, a show they both have is listed once, as
        Sonarr's (it knows the series type and alternate titles); merged=False
        keeps every source's copy, for looking a series up by any of its keys.
        """
        out: list[Series] = []
        errors: dict[str, str] = {}
        for src in self.sources():
            try:
                out.extend(self._cached(f"series:{src}", partial(self._series_of, src)))
            except ClientError as exc:
                errors[src] = str(exc)
        if merged:
            out = _merge_series(out)
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
        series, _ = self.list_series(merged=False)
        for s in series:
            if s.key == key:
                return s
        raise NotFoundError(f"series {key} not found")

    # -- movies ----------------------------------------------------------------
    def list_movies(self, merged: bool = True) -> tuple[list[Movie], dict[str, str]]:
        """All movies plus {source: error}; sources: radarr, plex movie sections, folder.

        The same file seen by Radarr and Plex is listed once (Radarr's entry,
        with Plex's collections added); a second edition is a second file and
        stays. merged=False keeps every copy, for lookups by any key.
        """
        out: list[Movie] = []
        errors: dict[str, str] = {}
        for src in self._movie_sources():
            try:
                out.extend(self._cached(f"movies:{src}", partial(self._movies_of, src)))
            except ClientError as exc:
                errors[src] = str(exc)
        if merged:
            out = _merge_movies(out)
        return sorted(out, key=lambda m: m.title.lower()), errors

    def get_movie(self, key: str) -> Movie:
        movies, _ = self.list_movies(merged=False)
        for m in movies:
            if m.key == key:
                return m
        raise NotFoundError(f"movie {key} not found")

    def _movie_sources(self) -> list[str]:
        lib = self.cfg.library
        return [n for n, on in (("radarr", lib.radarr.url), ("plex", lib.plex.url),
                                ("folder", lib.movies.path)) if on]

    def _movies_of(self, src: str) -> list[Movie]:
        if src == "folder":
            return scan.list_movies_folder(Path(self.cfg.library.movies.path))
        if src == "radarr":
            radarr = self.radarr()
            assert radarr is not None
            return [self._radarr_movie(m) for m in radarr.list_movies()]
        plex = self.plex()
        assert plex is not None
        want = set(self.cfg.library.plex.movie_sections)
        found: list[Movie] = []
        for sec in plex.sections():
            listed = not want or sec["key"] in want or sec["title"] in want
            if sec["type"] == "movie" and listed:
                found.extend(self._plex_movie(m) for m in plex.movies(sec["key"]))
        return found

    def _radarr_movie(self, m: dict[str, Any]) -> Movie:
        f = m.get("movieFile") or {}
        runtime = _hms(((f.get("mediaInfo") or {}).get("runTime")) or "")
        return Movie(key=f"radarr:{m['id']}", source="radarr", title=m.get("title", ""),
                     year=m.get("year") or None, tmdb_id=m.get("tmdbId") or None,
                     file=self._map(f["path"]) if f.get("path") else None, duration=runtime)

    def _plex_movie(self, m: dict[str, Any]) -> Movie:
        ids = {g["id"].split("://")[0]: g["id"].split("://")[1] for g in m.get("Guid", [])
               if "://" in g.get("id", "")}
        media = (m.get("Media") or [{}])[0]
        part = (media.get("Part") or [{}])[0]
        ms = media.get("duration") or m.get("duration")
        tmdb = int(ids["tmdb"]) if ids.get("tmdb", "").isdigit() else None
        return Movie(key=f"plexmovie:{m['ratingKey']}", source="plex", title=m.get("title", ""),
                     year=m.get("year"), tmdb_id=tmdb,
                     file=self._map(part["file"]) if part.get("file") else None,
                     duration=ms / 1000 if ms else None,
                     collections=[c.get("tag", "") for c in m.get("Collection", [])
                                  if c.get("tag")])

    def find_movie(self, tmdb_id: int | None, title: str = "", edition: str = "") -> Movie | None:
        """Resolve a movie source: by TMDB id, then title; edition prefers a matching name."""
        movies, _ = self.list_movies()
        cands = [m for m in movies if tmdb_id and m.tmdb_id == tmdb_id]
        if not cands and title:
            cands = [m for m in movies if m.title.lower() == title.lower()]
        if edition and len(cands) > 1:
            pref = [m for m in cands
                    if edition.lower() in m.title.lower()
                    or (m.file and edition.lower() in Path(m.file).stem.lower())]
            if pref:
                cands = pref
        return cands[0] if cands else None

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


# -- one entry per show / file across sources -------------------------------------
def _title_key(title: str, year: int | None) -> tuple[str, int | None]:
    return ("".join(ch for ch in title.lower() if ch.isalnum()), year)


def _merge_series(items: list[Series]) -> list[Series]:
    """Drop later sources' copies of a show already listed (same TVDB id, else title + year).

    Sources come in priority order (Sonarr, Plex, folder); the kept entry
    picks up ids it lacked from the dropped copy.
    """
    kept: list[Series] = []
    by_tvdb: dict[int, Series] = {}
    by_title: dict[tuple[str, int | None], Series] = {}
    for s in items:
        tk = _title_key(s.title, s.year)
        hit = (by_tvdb.get(s.tvdb_id) if s.tvdb_id else None) or by_title.get(tk)
        if hit is not None and (not hit.tvdb_id or not s.tvdb_id or hit.tvdb_id == s.tvdb_id):
            hit.tvdb_id = hit.tvdb_id or s.tvdb_id
            hit.tmdb_id = hit.tmdb_id or s.tmdb_id
            hit.episode_count = hit.episode_count or s.episode_count
            if hit.tvdb_id:
                by_tvdb.setdefault(hit.tvdb_id, hit)
            continue
        kept.append(s)
        if s.tvdb_id:
            by_tvdb.setdefault(s.tvdb_id, s)
        by_title.setdefault(tk, s)
    return kept


def _file_key(path: str) -> str:
    # Radarr and Plex may spell one file with different separators (a Windows
    # server, a path mapping); on Linux normpath leaves backslashes alone.
    return os.path.normcase(os.path.normpath(path.replace("\\", "/")))


def _merge_movies(items: list[Movie]) -> list[Movie]:
    """One entry per file: Radarr's, with Plex's collections, tmdb id and runtime added.

    Two files of one film (Theatrical and Extended) stay two entries. An
    entry with no file (Radarr still wanting it) is dropped when another
    source has the film on disk, and replaced by that copy.
    """
    kept: list[Movie] = []
    by_file: dict[str, Movie] = {}
    fileless: dict[object, Movie] = {}

    def ids(m: Movie) -> list[object]:
        return ([("tmdb", m.tmdb_id)] if m.tmdb_id else []) + [_title_key(m.title, m.year)]

    def absorb(into: Movie, other: Movie) -> None:
        into.tmdb_id = into.tmdb_id or other.tmdb_id
        into.duration = into.duration or other.duration
        into.collections = list(dict.fromkeys([*into.collections, *other.collections]))

    for m in items:
        if m.file:
            fk = _file_key(m.file)
            if fk in by_file:
                absorb(by_file[fk], m)
                continue
            stale = next((fileless.pop(i) for i in ids(m) if i in fileless), None)
            if stale is None:
                # a file renamed since Radarr last looked: its path is gone, this one is real
                stale = next((k for k in kept if k.file and set(ids(k)) & set(ids(m))
                              and not os.path.exists(k.file) and os.path.exists(m.file)), None)
                if stale is not None:
                    by_file.pop(_file_key(stale.file or ""), None)
            if stale is not None:
                kept.remove(stale)
                for i in ids(stale):
                    fileless.pop(i, None)
                absorb(m, stale)
            kept.append(m)
            by_file[fk] = m
            continue
        on_disk = next((k for k in kept if k.file and set(ids(k)) & set(ids(m))), None)
        if on_disk is not None or any(i in fileless for i in ids(m)):
            continue
        kept.append(m)
        for i in ids(m):
            fileless[i] = m
    return kept

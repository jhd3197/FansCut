"""The app's brain: ties config, SQLite state, library, catalogues, labels, planner and renderer.

Routes stay thin and call into this; so do the job handlers.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import itertools
import json
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import requests

from . import ai as ai_mod
from . import art, notify
from . import recipe as recipe_mod
from .art import ArtStore, art_ref
from .catalogues import match as catalogue_match
from .catalogues.http import CachedHttp
from .config import Config
from .db import Database, dumps
from .errors import ClientError, FansCutError, FFmpegError, NotFoundError
from .fillers import anifiller, jikan_flags
from .fillers import labels as labelling
from .fingerprint import detect
from .fingerprint.cache import FingerprintCache
from .fsutil import atomic_write
from .jobs import JobContext, JobStore, Worker
from .library.service import Library
from .models import LABELS, TRIM_KINDS, Episode
from .net import apply_proxy
from .planner import PRESETS, CutOptions, CutPlan, PlanEpisode, plan_cut
from .render import ffmpeg, plan, run
from .render.cache import RenderStore, render_hash
from .scheduler import Scheduler

UNSET: Any = object()  # "leave this field alone", distinct from None (= clear it)
WHOLE = -1  # matches.season value for "the whole series"
# PQ / HLG to SDR for stills (zimg): linear light, BT.709 primaries, Hable curve
HDR_TO_SDR = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
              "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
CROP_RE = re.compile(r"^\d{1,5}:\d{1,5}:\d{1,5}:\d{1,5}$")  # cropdetect's w:h:x:y


def _opts_from(base: dict[str, Any], extra: dict[str, Any] | None) -> CutOptions:
    known = {f.name for f in fields(CutOptions)}
    merged = {**base, **{k: v for k, v in (extra or {}).items() if v is not None}}
    unknown = set(merged) - known
    if unknown:
        raise FansCutError(f"unknown cut option(s): {', '.join(sorted(unknown))}")
    if merged.get("preset") not in PRESETS:
        raise FansCutError(f"unknown preset {merged.get('preset')!r}")
    if "trim_kinds" in merged:
        merged["trim_kinds"] = tuple(merged["trim_kinds"])
    return CutOptions(**merged)


class _State:
    """Everything apply_config builds from one config. A job pins the State it
    started with, so a settings save mid-render cannot swap the library or the
    ffmpeg settings out from under it."""

    __slots__ = ("art", "cfg", "http_anilist", "http_jikan", "library", "prober")
    cfg: Config

    def __init__(self, **kw: Any):
        for k, v in kw.items():
            setattr(self, k, v)


_pinned = threading.local()


def _state_attr(name: str) -> Any:
    def fget(self: Core) -> Any:
        st = getattr(_pinned, "state", None)
        return getattr(st if st is not None and getattr(_pinned, "core", None) is self
                       else self._live, name)

    def fset(self: Core, value: Any) -> None:  # tests swap in fakes
        setattr(self._live, name, value)

    return property(fget, fset)


class Core:
    cfg: Config = _state_attr("cfg")
    prober: ffmpeg.Prober = _state_attr("prober")
    library: Library = _state_attr("library")
    art: ArtStore = _state_attr("art")
    http_jikan: CachedHttp = _state_attr("http_jikan")
    http_anilist: CachedHttp = _state_attr("http_anilist")

    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.db = db
        self._own_session = session is None
        self.session = session or requests.Session()
        self.jobs = JobStore(db)
        self.renders = RenderStore(db)
        handlers = {"render": self._job_render, "detect": self._job_detect,
                    "timeline_sync": self._job_timeline_sync,
                    "timeline_render": self._job_timeline_render,
                    "find_similar": self._job_find_similar}
        from .scenes.service import SceneService

        self.scenes = SceneService(self)
        from .posters import Posters

        self.posters = Posters(self)
        handlers.update({
            "scene_index": self.scenes.job_index,
            "scene_tag": self.scenes.job_ai,
            "scene_signals": self.scenes.job_signals,
            "scene_label": self.scenes.job_label,
        })
        self.worker = Worker(self.jobs, {k: self._pin(fn) for k, fn in handlers.items()},
                             on_finish=self._job_finished)
        self._apply_lock = threading.Lock()
        self.apply_config(cfg)
        self.scheduler = Scheduler({
            "prune": ("Trim the art cache, finished jobs and old catalogue replies",
                      lambda _c: self.prune()),
            "sweep_renders": ("Delete renders not marked keep, past their days",
                              lambda _c: self.sweep_renders()),
            "library_refresh": ("Re-list Sonarr, Radarr, Plex and folders in the background",
                                lambda _c: self.library.warm()),
            "fillers_refresh": ("Download the latest AniFiller data",
                                lambda _c: self.refresh_fillers()),
            "cut_updates": ("Look for updates to installed community cuts",
                            lambda _c: self.cut_updates()),
            "library_check": ("Check the whole library for problems (Health page)",
                              lambda _c: self.library_check()),
        }, lambda: self.cfg.schedule.model_dump())

    def prune(self) -> dict[str, Any]:
        """Housekeeping: expired art and the oldest past the size cap, finished jobs
        older than 30 days, catalogue replies far past their cache time."""
        http_days = max(60, self.cfg.catalogues.cache_days * 4)
        return {"art_removed": self.art.prune(), **self.db.prune(30, http_days)}

    def _pin(self, fn: Any) -> Any:
        """Run a job handler against the settings that were live when it started."""
        def run(payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
            _pinned.state, _pinned.core = self._live, self
            try:
                result: dict[str, Any] = fn(payload, ctx)
                return result
            finally:
                _pinned.state = _pinned.core = None
        return run

    def _job_finished(self, job: dict[str, Any]) -> None:
        notify.job_finished(self._live.cfg.notify, job)

    def apply_config(self, cfg: Config) -> None:
        """(Re)build everything that depends on settings; at start and after a settings save."""
        with self._apply_lock:
            prober = ffmpeg.Prober(cfg.ffmpeg, self.db)
            library = Library(cfg, prober)
            art.CACHE_REMOTE = cfg.art.cache_remote
            store = ArtStore(library, cfg.data_dir / "art",
                             max_bytes=max(16, cfg.art.cache_mb) * 1024 * 1024,
                             session=self.session)
            if self._own_session:  # internet calls (catalogues, GitHub) use network.proxy
                self.session.proxies = {}
                apply_proxy(self.session, cfg.network)
            cat = cfg.catalogues
            # one assignment: a reader sees the old State or the new one, never a mix
            self._live = _State(
                cfg=cfg, prober=prober, library=library, art=store,
                http_jikan=CachedHttp(self.db, 0.4, cat.cache_days, cat.offline, self.session),
                http_anilist=CachedHttp(self.db, 0.8, cat.cache_days, cat.offline, self.session))
            self._fillers: anifiller.FillerIndex | None = None

    # -- filler data ----------------------------------------------------------
    @property
    def fillers(self) -> anifiller.FillerIndex:
        if self._fillers is None:
            self._fillers = anifiller.load_dir(self.cfg.fillers_dir)
        return self._fillers

    def fillers_status(self) -> dict[str, Any]:
        f = self.fillers
        return {"dir": str(self.cfg.fillers_dir), "files": f.files, "shows": f.shows,
                "url": self.cfg.fillers.url}

    def refresh_fillers(self) -> dict[str, Any]:
        if self.cfg.catalogues.offline:
            return {"ok": False, "message": "offline mode is on"}
        res = anifiller.refresh(self.cfg.fillers_dir, self.cfg.fillers.url)
        self._fillers = None
        return {**res, **self.fillers_status()}

    # -- series matching ------------------------------------------------------
    def matches(self, key: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM matches WHERE series_key = ? ORDER BY season", (key,))]

    def match_for(self, key: str, season: int) -> tuple[dict[str, Any] | None, bool]:
        """(match row, season_scoped): an exact season match wins over the series-wide one."""
        rows = {r["season"]: r for r in self.matches(key)}
        if season in rows:
            return rows[season], True
        return rows.get(WHOLE), False

    def set_match(self, key: str, mal_id: int | None, anilist_id: int | None, title: str = "",
                  season: int = WHOLE, source: str = "manual") -> dict[str, Any]:
        self.library.get_series(key)
        self.db.execute(
            "INSERT OR REPLACE INTO matches (series_key, season, mal_id, anilist_id, title, source)"
            " VALUES (?,?,?,?,?,?)", (key, season, mal_id, anilist_id, title, source))
        return self.matches_view(key)

    def clear_match(self, key: str, season: int = WHOLE) -> None:
        self.db.execute("DELETE FROM matches WHERE series_key = ? AND season = ?", (key, season))

    def matches_view(self, key: str) -> dict[str, Any]:
        return {"series_key": key, "matches": self.matches(key)}

    def candidates(self, key: str, query: str | None) -> list[dict[str, Any]]:
        series = self.library.get_series(key)
        return catalogue_match.candidates(self._catalogue_http(), series, query)

    def auto_match(self, key: str) -> dict[str, Any] | None:
        series = self.library.get_series(key)
        best = catalogue_match.auto_match(self._catalogue_http(), series)
        if best:
            self.set_match(key, best.get("mal_id"), best.get("anilist_id"), best["title"],
                           source="auto")
        return best

    def _catalogue_http(self) -> CachedHttp:
        # AniList and Jikan each have their own limiter; candidates() sends to both
        # through one object, so give it the slower of the two.
        return self.http_anilist

    # -- labels ---------------------------------------------------------------
    def labels_for(
        self, key: str, episodes: list[Episode]
    ) -> tuple[dict[str, labelling.LabelResult], dict[str, Any]]:
        overrides = {r["episode_id"]: r["label"] for r in self.db.query(
            "SELECT episode_id, label FROM overrides WHERE series_key = ? AND label IS NOT NULL",
            (key,))}
        absolute = labelling.catalogue_numbers(episodes)
        results: dict[str, labelling.LabelResult] = {}
        sources: dict[str, Any] = {}
        by_group: dict[int, list[Episode]] = {}
        for e in episodes:
            _, scoped = self.match_for(key, e.season)
            by_group.setdefault(e.season if scoped else WHOLE, []).append(e)
        for group, eps in by_group.items():
            match, scoped = self.match_for(key, eps[0].season)
            numbers = {e.id: (e.number if scoped and e.season != 0 else absolute[e.id])
                       for e in eps}
            mal = match["mal_id"] if match else None
            ani = match["anilist_id"] if match else None
            show = self.fillers.find(mal, ani)
            jikan_data = None
            wanted = [n for n in numbers.values() if n is not None]
            covered = show is not None and all(n in show.labels for n in wanted)
            if mal and not covered and self.cfg.fillers.use_jikan:
                jikan_data = jikan_flags.fetch(self.http_jikan, mal)
            results.update(labelling.resolve(
                eps, numbers, show.labels if show else None, jikan_data, overrides))
            sources[str(group)] = {"mal_id": mal, "anilist_id": ani,
                                   "anifiller": show is not None, "jikan": jikan_data is not None}
        return results, sources

    # -- per-episode state ----------------------------------------------------
    def set_override(self, key: str, episode_id: str, label: Any = UNSET,
                     include: Any = UNSET) -> None:
        if label is not UNSET and label is not None and label not in LABELS:
            raise FansCutError(f"bad label {label!r}")
        row = self.db.one(
            "SELECT label, include FROM overrides WHERE series_key=? AND episode_id=?",
            (key, episode_id))
        cur_label = row["label"] if row else None
        cur_inc = row["include"] if row else None
        new_label = cur_label if label is UNSET else label
        new_inc = cur_inc if include is UNSET else (None if include is None else int(include))
        if new_label is None and new_inc is None:
            self.db.execute("DELETE FROM overrides WHERE series_key=? AND episode_id=?",
                            (key, episode_id))
        else:
            self.db.execute(
                "INSERT OR REPLACE INTO overrides (series_key, episode_id, label, include)"
                " VALUES (?,?,?,?)", (key, episode_id, new_label, new_inc))

    def overrides(self, key: str) -> dict[str, dict[str, Any]]:
        return {r["episode_id"]: {"label": r["label"],
                                  "include": None if r["include"] is None else bool(r["include"])}
                for r in self.db.query("SELECT * FROM overrides WHERE series_key = ?", (key,))}

    def set_trim(self, key: str, episode_id: str, kind: str,
                 start: float | None, end: float | None) -> None:
        """start/end None = cleared by the user (detection will not bring it back)."""
        if kind not in TRIM_KINDS:
            raise FansCutError(f"unknown trim kind {kind!r}")
        if (start is None) != (end is None):
            raise FansCutError("give both start and end, or neither to clear")
        if start is not None and end is not None and (start < 0 or end <= start):
            raise FansCutError("end must be after start, and start must not be negative")
        self.db.execute(
            "INSERT OR REPLACE INTO trims (series_key, episode_id, kind, start, end, source)"
            " VALUES (?,?,?,?,?,'user')", (key, episode_id, kind, start, end))

    def reset_trim(self, key: str, episode_id: str, kind: str) -> None:
        self.db.execute("DELETE FROM trims WHERE series_key=? AND episode_id=? AND kind=?",
                        (key, episode_id, kind))

    def trims(self, key: str) -> dict[str, dict[str, dict[str, Any]]]:
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for r in self.db.query("SELECT * FROM trims WHERE series_key = ?", (key,)):
            out.setdefault(r["episode_id"], {})[r["kind"]] = {
                "start": r["start"], "end": r["end"], "source": r["source"]}
        return out

    # -- custom segments ("my own cut") -----------------------------------------
    def custom_segments(self, key: str) -> dict[str, list[list[float]]]:
        return {r["episode_id"]: json.loads(r["ranges"]) for r in self.db.query(
            "SELECT * FROM custom_segments WHERE series_key = ?", (key,))}

    def set_segments(self, key: str, episode_id: str,
                     ranges: list[list[float]] | None) -> None:
        """ranges = kept [start, end] pairs in seconds; empty or None clears."""
        if not ranges:
            self.db.execute(
                "DELETE FROM custom_segments WHERE series_key=? AND episode_id=?",
                (key, episode_id))
            return
        clean = []
        for r in ranges:
            if not isinstance(r, list | tuple) or len(r) != 2:
                raise FansCutError("each segment must be [start, end]")
            s, e = r[0], r[1]
            if isinstance(s, bool) or isinstance(e, bool) \
                    or not isinstance(s, int | float) or not isinstance(e, int | float):
                raise FansCutError("segment bounds must be numbers")
            if s < 0 or e <= s:
                raise FansCutError("segment end must be after start, and start not negative")
            clean.append([float(s), float(e)])
        self.db.execute(
            "INSERT OR REPLACE INTO custom_segments (series_key, episode_id, ranges)"
            " VALUES (?,?,?)", (key, episode_id, dumps(sorted(clean))))

    # -- series settings ------------------------------------------------------
    def settings(self, key: str) -> dict[str, Any]:
        defaults = self.cfg.cut.model_dump()
        row = self.db.one("SELECT settings FROM series_settings WHERE series_key = ?", (key,))
        return {**defaults, **(json.loads(row["settings"]) if row else {})}

    def save_settings(self, key: str, changes: dict[str, Any]) -> dict[str, Any]:
        merged = {**self.settings(key), **changes}
        _opts_from(merged, None)  # validate
        allowed = set(self.cfg.cut.model_dump())
        stored = {k: v for k, v in merged.items() if k in allowed}
        self.db.execute(
            "INSERT OR REPLACE INTO series_settings (series_key, settings) VALUES (?,?)",
            (key, dumps(stored)))
        return stored

    # -- views and the cut ----------------------------------------------------
    def _in_seasons(self, episodes: list[Episode], seasons: list[int] | None) -> list[Episode]:
        return [e for e in episodes if not seasons or e.season in seasons]

    def episode_view(self, key: str) -> dict[str, Any]:
        series = self.library.get_series(key)
        episodes = self.library.episodes(key)
        labels, sources = self.labels_for(key, episodes)
        overrides, trims = self.overrides(key), self.trims(key)
        segments = self.custom_segments(key)
        settings = self.settings(key)
        opts = _opts_from(settings, None)
        plan_eps = self._plan_episodes(episodes, labels, overrides, trims, segments)
        from .planner import wants
        rows = []
        for e, pe in zip(episodes, plan_eps, strict=True):
            keep, why = wants(pe, opts)
            if pe.segments is not None and pe.include is not False:
                keep, why = True, "custom segments"
            rows.append({
                "id": e.id, "season": e.season, "number": e.number, "absolute": e.absolute,
                "title": e.title, "air_date": e.air_date, "duration": e.duration,
                "has_file": bool(e.file),
                "label": labels[e.id].label, "label_source": labels[e.id].source,
                "override": overrides.get(e.id), "include": keep, "include_reason": why,
                "trims": trims.get(e.id, {}), "segments": segments.get(e.id),
            })
        return {"series": asdict(series), "settings": settings, "filler_sources": sources,
                "matches": self.matches(key), "episodes": rows}

    def _plan_episodes(self, episodes: list[Episode], labels: dict[str, labelling.LabelResult],
                       overrides: dict[str, dict[str, Any]],
                       trims: dict[str, dict[str, dict[str, Any]]],
                       segments: dict[str, list[list[float]]]) -> list[PlanEpisode]:
        out = []
        for e in episodes:
            t = {k: (v["start"], v["end"]) for k, v in trims.get(e.id, {}).items()
                 if v["start"] is not None and v["end"] is not None}
            out.append(PlanEpisode(
                id=e.id, number=e.number, season=e.season, file=e.file, duration=e.duration,
                label=labels[e.id].label, include=(overrides.get(e.id) or {}).get("include"),
                trims=t, title=e.title,
                segments=[(r[0], r[1]) for r in segments[e.id]] if e.id in segments else None))
        return out

    def build_cut(self, key: str, seasons: list[int] | None = None,
                  options: dict[str, Any] | None = None) -> tuple[CutPlan, list[Episode]]:
        episodes = self._in_seasons(self.library.episodes(key), seasons)
        if not episodes:
            raise NotFoundError("no episodes in that selection")
        labels, _ = self.labels_for(key, episodes)
        opts = _opts_from(self.settings(key), options)
        plan_eps = self._plan_episodes(episodes, labels, self.overrides(key), self.trims(key),
                                       self.custom_segments(key))
        return plan_cut(plan_eps, opts), episodes

    # -- rendering ------------------------------------------------------------
    def render_plan(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                    mode: plan.Mode, workdir: Path) -> plan.RenderPlan:
        cut, episodes = self.build_cut(key, seasons, options)
        series = self.library.get_series(key)
        suffix = f" - S{'+'.join(f'{s:02d}' for s in seasons)}" if seasons else ""
        stem = plan.safe_name(f"{series.title}{suffix} - fan cut")
        titles = {e.id: f"{e.id} {e.title}".strip() for e in episodes}
        provisional = self.cfg.output_dir / f"{stem}.{self.cfg.ffmpeg.container}"
        rp = plan.build_plan(cut, self.cfg.ffmpeg, provisional, workdir, self.prober, titles, mode,
                             hdr_params=self._hdr_params)
        # The output is named after a hash of everything that shapes its bytes, so
        # re-rendering an unchanged cut finds the file already there (cache hit).
        h = render_hash(key, cut, rp.mode, self.cfg.ffmpeg, rp.chapters)
        out = self.cfg.output_dir / f"{stem}.{h[:10]}.{self.cfg.ffmpeg.container}"
        tmp = out.with_name(f"{out.stem}.part{out.suffix}")
        rp.output, rp.output_tmp, rp.cache_key = str(out), str(tmp), h
        rp.command[-1] = str(tmp)
        return rp

    def dry_run(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                mode: plan.Mode) -> dict[str, Any]:
        rp = self.render_plan(key, seasons, options, mode, Path("<workdir>"))
        cut, _ = self.build_cut(key, seasons, options)
        return {"cut": cut.to_dict(), "render": rp.to_dict(),
                "cached": self.renders.hit(rp.cache_key) is not None}

    def submit_render(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                      mode: str, keep: bool | None = None, refresh: bool = False,
                      after: list[str] | None = None) -> dict[str, Any]:
        """Queue a render. `after`: job ids it must wait for (detection of its seasons)."""
        series = self.library.get_series(key)
        self.build_cut(key, seasons, options)  # fail fast on a bad request
        payload: dict[str, Any] = {"series_key": key, "seasons": seasons, "options": options,
                                   "mode": mode, "keep": keep, "refresh": refresh,
                                   "title": series.title}
        if after:
            payload["after"] = after
        return self.worker.submit("render", f"Render {series.title}", payload)

    def _job_render(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        keep = payload.get("keep")
        if keep is None:
            keep = self.cfg.render.keep
        with tempfile.TemporaryDirectory(prefix="render-", dir=self.cfg.data_dir) as tmp:
            rp = self.render_plan(payload["series_key"], payload.get("seasons"),
                                  payload.get("options"), payload.get("mode", "auto"), Path(tmp))
            if not payload.get("refresh"):
                hit = self.renders.hit(rp.cache_key)
                if hit:
                    return {"output": hit["path"], "mode": rp.mode, "cached": True,
                            "cache_key": rp.cache_key, "keep": hit["keep"],
                            "message": f"cached: {hit['path']}"}
            ctx.progress(0, f"rendering ({rp.mode}): {rp.reason}")
            out = run.execute(rp, Path(tmp), ctx.progress, ctx.cancel)
        row = self.renders.add(rp.cache_key, payload["series_key"], payload.get("title", ""),
                               out, rp.mode, bool(keep), job_id=ctx.job_id)
        swept = self.sweep_renders()
        return {"output": out, "mode": rp.mode, "cached": False, "cache_key": rp.cache_key,
                "keep": row["keep"], "swept": swept["removed"], "message": f"wrote {out}"}

    # -- rendered output (cache, keep/evict) ------------------------------------
    def list_renders(self, series_key: str | None = None) -> list[dict[str, Any]]:
        return self.renders.all(series_key)

    def set_render_keep(self, h: str, keep: bool) -> dict[str, Any]:
        row = self.renders.set_keep(h, keep)
        if row is None:
            raise NotFoundError(f"render {h} not found")
        return row

    def delete_render(self, h: str) -> None:
        row = self.renders.remove(h)
        if row is None:
            raise NotFoundError(f"render {h} not found")
        with contextlib.suppress(OSError):
            Path(row["path"]).unlink()

    def sweep_renders(self) -> dict[str, Any]:
        """Delete non-kept renders older than render.ephemeral_days (0 = never)."""
        removed = []
        days = self.cfg.render.ephemeral_days
        if days > 0:
            cutoff = (_dt.datetime.now() - _dt.timedelta(days=days)).isoformat(timespec="seconds")
            for r in self.renders.due(cutoff):
                with contextlib.suppress(OSError):
                    Path(r["path"]).unlink()
                self.renders.remove(r["hash"])
                removed.append(r["path"])
        return {"removed": removed}

    # -- Sonarr webhook (auto-render on new episodes) ---------------------------
    def handle_sonarr_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Sonarr 'Download'/'Upgrade' -> detect trims + render the affected seasons.

        Never raises: Sonarr retries on non-2xx, so problems come back in the body.
        """
        event = payload.get("eventType", "")
        if event == "Test":
            return {"ok": True, "action": "test"}
        if not self.cfg.automation.sonarr_webhook:
            return {"ok": True, "action": "ignored",
                    "reason": "automation.sonarr_webhook is off"}
        if event not in ("Download", "Upgrade"):
            return {"ok": True, "action": "ignored", "reason": f"event {event or 'unknown'}"}
        sid = (payload.get("series") or {}).get("id")
        if sid is None:
            return {"ok": True, "action": "ignored", "reason": "no series id in payload"}
        key = f"sonarr:{sid}"
        self.library.refresh()  # the new file just landed; don't trust the 30s cache
        try:
            series = self.library.get_series(key)
        except NotFoundError:
            return {"ok": True, "action": "ignored", "reason": f"{key} not in the library"}
        seasons = sorted({e["seasonNumber"] for e in payload.get("episodes") or []
                          if isinstance(e.get("seasonNumber"), int)}) or None
        jobs = []
        try:
            if self.cfg.automation.auto_detect:
                for s in seasons or []:
                    with_files = [e for e in self.library.episodes(key)
                                  if e.season == s and e.file and e.duration]
                    if len(with_files) >= 2:
                        jobs.append(self.submit_detect(key, s))
            jobs.append(self.submit_render(key, seasons, None, "auto",
                                           keep=self.cfg.automation.keep,
                                           after=[j["id"] for j in jobs]))
        except FansCutError as exc:
            return {"ok": False, "action": "error", "reason": str(exc)}
        return {"ok": True, "action": "queued", "series": series.title, "seasons": seasons,
                "jobs": [j["id"] for j in jobs]}

    # -- OP/ED detection ------------------------------------------------------
    def submit_detect(self, key: str, season: int) -> dict[str, Any]:
        series = self.library.get_series(key)
        return self.worker.submit("detect", f"Find OP/ED in {series.title} S{season:02d}",
                                  {"series_key": key, "season": season})

    def _job_detect(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        key, season = payload["series_key"], payload["season"]
        eps = [e for e in self.library.episodes(key)
               if e.season == season and e.file and e.duration]
        if len(eps) < 2:
            raise FansCutError("need at least two episodes with files in the season")
        files = [(e.id, e.file or "", e.duration or 0.0) for e in eps]
        found = detect.analyze_files(files, self.cfg.fingerprint, self.cfg.ffmpeg,
                                     lambda p: ctx.progress(p),
                                     cache=FingerprintCache(self.db))
        self.store_detected(key, found)
        hits = sum(1 for v in found.values() if v)
        return {"episodes": len(eps),
                "message": f"Opening or ending found in {hits} of {len(eps)} episodes",
                "found": {k: {t: list(r) for t, r in v.items()} for k, v in found.items()}}

    def store_detected(self, key: str, found: dict[str, dict[str, tuple[float, float]]]) -> None:
        """Replace auto trims; anything the user has touched stays as they left it."""
        with self.db.transaction():  # a crash mid-way must not leave episodes half-cleared
            for eid, ranges in found.items():
                self.db.execute(
                    "DELETE FROM trims WHERE series_key=? AND episode_id=? AND source='auto'",
                    (key, eid))
                for kind, (s, e) in ranges.items():
                    self.db.execute(
                        "INSERT OR IGNORE INTO trims (series_key, episode_id, kind, start, end,"
                        " source) VALUES (?,?,?,?,?,'auto')", (key, eid, kind, s, e))

    # -- timelines (chronological recipes) --------------------------------------
    def _timeline_row(self, r: Any, with_doc: bool = False) -> dict[str, Any]:
        doc = json.loads(r["doc"])
        out = {"id": r["id"], "series_key": r["series_key"], "title": r["title"],
               "parts": len(doc["parts"]), "sources": len(doc["sources"]),
               "category": doc.get("category") or "",
               "auto_rebuild": bool(r["auto_rebuild"]),
               "updated_at": r["updated_at"],
               "synced": self.db.one(
                   "SELECT timeline_id FROM timeline_syncs WHERE timeline_id = ?",
                   (r["id"],)) is not None}
        if with_doc:
            out["doc"] = doc
        return out

    def list_timelines(self, key: str) -> list[dict[str, Any]]:
        self.library.get_series(key)
        return [self._timeline_row(r) for r in self.db.query(
            "SELECT * FROM timelines WHERE series_key = ? ORDER BY created_at", (key,))]

    def create_timeline(self, key: str, doc: Any) -> dict[str, Any]:
        from .timeline import recipe as tl_recipe

        if len(dumps(doc)) > tl_recipe.MAX_BYTES:
            raise FansCutError("recipe is over the 2 MB limit")
        clean = tl_recipe.validate(doc)
        files = self._source_files(key, clean)
        available = {src for src, (f, _d) in files.items() if f}
        tid = uuid.uuid4().hex[:12]
        now = _dt.datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT INTO timelines (id, series_key, title, doc, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)", (tid, key, clean["title"], dumps(clean), now, now))
        return {**self.get_timeline(tid), "missing": tl_recipe.missing_sources(clean, available)}

    def get_timeline(self, tid: str, with_doc: bool = True) -> dict[str, Any]:
        from .timeline import recipe as tl_recipe

        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        out = self._timeline_row(row, with_doc)
        if with_doc:
            files = self._source_files(row["series_key"], out["doc"])
            available = {src for src, (f, _d) in files.items() if f}
            out["missing"] = tl_recipe.missing_sources(out["doc"], available)
        return out

    def update_timeline(self, tid: str, doc: Any) -> dict[str, Any]:
        from .timeline import recipe as tl_recipe

        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        if len(dumps(doc)) > tl_recipe.MAX_BYTES:
            raise FansCutError("recipe is over the 2 MB limit")
        clean = tl_recipe.validate(doc)
        with self.db.transaction():
            self.db.execute("UPDATE timelines SET title = ?, doc = ?, updated_at = ? WHERE id = ?",
                            (clean["title"], dumps(clean),
                             _dt.datetime.now().isoformat(timespec="seconds"), tid))
            self.db.execute("DELETE FROM timeline_syncs WHERE timeline_id = ?", (tid,))
        return self.get_timeline(tid)

    def delete_timeline(self, tid: str) -> None:
        if self.db.one("SELECT id FROM timelines WHERE id = ?", (tid,)) is None:
            raise NotFoundError(f"timeline {tid} not found")
        # its sync report and install record go with it (ON DELETE CASCADE)
        self.db.execute("DELETE FROM timelines WHERE id = ?", (tid,))

    # -- timeline anchors: export (author) and sync (user) ------------------------
    def export_timeline(self, tid: str, kinds: tuple[str, ...] = ("a", "v")) -> dict[str, Any]:
        """The recipe with anchors computed from the author's own files."""
        from .timeline import anchors as anchors_mod

        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        doc = json.loads(row["doc"])
        files = self._source_files(row["series_key"], doc)
        cache = anchors_mod.AnchorCache(self.db)
        missing: list[str] = []
        for part in doc["parts"]:
            for seg in part["segments"]:
                path, _dur = files.get(seg["src"], (None, None))
                if not path or not Path(path).is_file():
                    if seg["src"] not in missing:
                        missing.append(seg["src"])
                    continue
                for side in ("start", "end"):
                    a = anchors_mod.make_anchors(self.cfg.ffmpeg, path, seg[side],
                                                 self.cfg.fingerprint, kinds, cache)
                    seg.setdefault("anchors", {})[side] = a
        out = {**doc, "_missing": sorted(missing)}
        return out

    def timeline_sync_report(self, tid: str) -> dict[str, Any] | None:
        row = self.db.one("SELECT report, updated_at FROM timeline_syncs WHERE timeline_id = ?",
                          (tid,))
        if row is None:
            return None
        report: dict[str, Any] = {**json.loads(row["report"]), "updated_at": row["updated_at"]}
        overrides = report.get("overrides") or {}
        for src in report.get("sources", {}).values():  # a nudged cut point wins
            for b in src.get("boundaries", []):
                key = f"{b['part']}:{b['segment']}:{b['side']}"
                if key in overrides:
                    b["auto"] = b.get("corrected")
                    b["corrected"] = overrides[key]
                    b["manual"] = True
        return report

    def set_cut_point(self, tid: str, part: int, segment: int, side: str,
                      t: float | None) -> dict[str, Any]:
        """Build → nudge: pin one cut point to time t in the user's copy (None = back to
        what sync found). Kept across re-syncs; a saved recipe edit resets the sync."""
        if side not in ("start", "end"):
            raise FansCutError("side is start or end")
        row = self.db.one("SELECT report FROM timeline_syncs WHERE timeline_id = ?", (tid,))
        if row is None:
            raise FansCutError("run sync first: POST /api/timelines/{id}/sync")
        report = json.loads(row["report"])
        found = next((b for s in report.get("sources", {}).values()
                      for b in s.get("boundaries", [])
                      if (b["part"], b["segment"], b["side"]) == (part, segment, side)), None)
        if found is None:
            raise NotFoundError(f"no cut point {part}.{segment} {side}")
        key = f"{part}:{segment}:{side}"
        overrides = report.setdefault("overrides", {})
        if t is None:
            overrides.pop(key, None)
        else:
            other = next((b for s in report.get("sources", {}).values()
                          for b in s.get("boundaries", [])
                          if (b["part"], b["segment"]) == (part, segment) and b["side"] != side),
                         None)
            if other is not None:
                o = overrides.get(f"{part}:{segment}:{other['side']}", other.get("corrected"))
                if o is not None and (t >= o if side == "start" else t <= o):
                    raise FansCutError("a segment's start must stay before its end")
            overrides[key] = round(float(t), 3)
        self.db.execute("UPDATE timeline_syncs SET report = ? WHERE timeline_id = ?",
                        (dumps(report), tid))
        return self.timeline_sync_report(tid) or {}

    def submit_timeline_sync(self, tid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        return self.worker.submit("timeline_sync", f"Sync {row['title']}", {"timeline_id": tid})

    def _job_timeline_sync(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        from .timeline import anchors as anchors_mod

        tid = payload["timeline_id"]
        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        doc = json.loads(row["doc"])
        files = self._source_files(row["series_key"], doc)
        # every cut point, grouped per source
        bounds: dict[str, list[dict[str, Any]]] = {}
        for pi, part in enumerate(doc["parts"], start=1):
            for si, seg in enumerate(part["segments"], start=1):
                for side in ("start", "end"):
                    bounds.setdefault(seg["src"], []).append({
                        "part": pi, "segment": si, "side": side, "t": seg[side],
                        "anchors": (seg.get("anchors") or {}).get(side)})
        total = sum(len(v) for v in bounds.values())
        done = 0
        sources: dict[str, Any] = {}
        for src, blist in sorted(bounds.items()):
            path, _dur = files.get(src, (None, None))
            entry = doc["sources"].get(src, {})
            meta = {"kind": entry.get("kind", "episode")}
            if entry.get("edition"):
                meta["edition"] = entry["edition"]
            if not path or not Path(path).is_file():
                sources[src] = {"matched": False, "reason": "no file in this library",
                                "boundaries": [], **meta}
                done += len(blist)
                continue
            results = []
            for b in blist:
                if b["anchors"]:
                    r = anchors_mod.find_in_file(self.cfg.ffmpeg, self.cfg.fingerprint,
                                                 path, b["t"], b["anchors"])
                else:
                    r = {"time": b["t"], "offset": None, "method": "none", "confidence": 0.0}
                results.append({k: b[k] for k in ("part", "segment", "side", "t")}
                               | {"anchored": bool(b["anchors"])} | r)
                done += 1
                ctx.progress(done / total, f"{src} {b['side']} of part {b['part']}")
            rec = anchors_mod.reconcile(results)
            matched = sum(1 for r in results if r["method"] != "none")
            # per-segment: anchors present but nothing matched = not in this edition
            segs: dict[str, str] = {}
            for (p, s), group in itertools.groupby(results,
                                                   key=lambda b: (b["part"], b["segment"])):
                g = list(group)
                anchored = [b for b in g if b["anchored"]]
                found = [b for b in anchored if b["method"] != "none"]
                if not anchored:
                    segs[f"{p}:{s}"] = "unanchored"
                elif not found:
                    segs[f"{p}:{s}"] = "missing"  # not in your edition
                elif len(found) < len(anchored):
                    segs[f"{p}:{s}"] = "partial"
                else:
                    segs[f"{p}:{s}"] = "ok"
            sources[src] = {"matched": matched > 0, "matched_boundaries": matched,
                            "total_boundaries": len(results), "segments": segs, **meta, **rec}
        report = {"timeline_id": tid, "sources": sources,
                  "unmatched_sources": [s for s, v in sources.items() if not v["matched"]]}
        old = self.db.one("SELECT report FROM timeline_syncs WHERE timeline_id = ?", (tid,))
        kept = (json.loads(old["report"]).get("overrides") or {}) if old else {}
        names = {f"{b['part']}:{b['segment']}:{b['side']}" for v in sources.values()
                 for b in v.get("boundaries", [])}
        if kept:
            report["overrides"] = {k: v for k, v in kept.items() if k in names}
        now = _dt.datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT OR REPLACE INTO timeline_syncs (timeline_id, report, updated_at)"
            " VALUES (?,?,?)", (tid, dumps(report), now))
        matched = sum(v.get("matched_boundaries", 0) for v in sources.values())
        check = f" · {total - matched} to check" if matched < total else ""
        return {"sources": len(sources), "boundaries": total,
                "unmatched": len(report["unmatched_sources"]),
                "message": f"{matched} of {total} cut points matched{check}"}

    # -- timeline rendering -------------------------------------------------------
    def _source_files(self, key: str, doc: dict[str, Any]
                      ) -> dict[str, tuple[str | None, float | None]]:
        """Every source in the recipe -> (file, duration) of the user's copy, or None."""
        try:
            episodes = {e.id: e for e in self.library.episodes(key)}
        except (NotFoundError, ClientError):
            episodes = {}  # movie-only timeline: no series attached
        out: dict[str, tuple[str | None, float | None]] = {}
        for src, s in doc["sources"].items():
            if s.get("kind") == "movie":
                m = self.library.find_movie(s.get("tmdb"), edition=s.get("edition", ""))
                out[src] = (m.file, m.duration) if m else (None, None)
            else:
                e = episodes.get(src)
                out[src] = (e.file, e.duration) if e else (None, None)
        return out

    def _skip_set(self, report: dict[str, Any]) -> set[tuple[int, int]]:
        """(part, segment) pairs the sync flagged as not in this edition."""
        out = set()
        for src in report.get("sources", {}).values():
            for ps, status in (src.get("segments") or {}).items():
                if status == "missing":
                    p, s = ps.split(":")
                    out.add((int(p), int(s)))
        return out
    def _timeline_ctx(self, tid: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
        row = self.db.one("SELECT * FROM timelines WHERE id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"timeline {tid} not found")
        report = self.timeline_sync_report(tid)
        return json.loads(row["doc"]), row["series_key"], report or {}

    def _audio_map(self, files: list[str]) -> tuple[dict[str, int], list[str]]:
        """Per-file index of the configured audio language; 0 = first track."""
        lang = self.cfg.render.language
        amap: dict[str, int] = {}
        warnings: list[str] = []
        if not lang:
            return amap, warnings
        for f in files:
            tracks = self.prober(f).get("audio_tracks") or []
            idx = next((i for i, t in enumerate(tracks)
                        if (t.get("language") or "").lower() == lang.lower()), None)
            if idx is None:
                warnings.append(f"no '{lang}' audio track in {Path(f).name}; using the first")
            elif idx > 0:
                amap[f] = idx
        return amap, warnings

    def _through_effectcraft(self, cut: Any, workdir: Path, ctx: JobContext, pi: int) -> Any:
        """Grade segments through EffectCraft when it's configured; others untouched."""
        from dataclasses import replace

        from .render import effectcraft

        cli = self.cfg.render.effectcraft_cli
        if not cli or not self.cfg.render.effectcraft_grade:
            return cut
        todo = [i for i, s in enumerate(cut.segments)
                if ((s.fx or {}).get("video") or {}).get("grade") and not s.fx.get("speed")]
        if not todo:
            return cut
        ctx.phase(2, 5, "Effect segments",
                  f"part {pi + 1} · {len(todo)} graded through EffectCraft")
        segs = list(cut.segments)
        for n, i in enumerate(todo, start=1):
            ctx.progress(0.0, f"part {pi + 1}: EffectCraft grade {n}/{len(todo)}")
            segs[i] = effectcraft.render_segment(cli, self.cfg.ffmpeg, segs[i],
                                                 self.prober(segs[i].file), workdir, f"ec{i}")
        return replace(cut, segments=segs)

    def _hdr_params(self, path: str) -> str:
        return ffmpeg.hdr10_params(self.cfg.ffmpeg, path)

    def _timeline_series_title(self, key: str, doc: dict[str, Any]) -> str:
        try:
            return self.library.get_series(key).title
        except NotFoundError:
            return doc["series"].get("title", "Timeline")  # movie-only timeline

    def _toggle_state(self, doc: dict[str, Any],
                      files: dict[str, tuple[str | None, float | None]],
                      variant: str | None, toggles: dict[str, bool] | None
                      ) -> tuple[set[tuple[int, int]], list[dict[str, Any]], str]:
        """(segments switched off, toggle view, variant label) for one render."""
        from .timeline import toggles as tg

        available = {k for k, (f, _d) in files.items() if f}
        values, view = tg.resolve(doc, available, variant, toggles)
        return tg.off_set(doc, values), view, tg.variant_label(doc, variant)

    def timeline_variants(self, tid: str) -> dict[str, Any]:
        """Toggles (enabled or not, and why) and every variant's runtime."""
        from .timeline import toggles as tg

        doc, key, report = self._timeline_ctx(tid)
        files = self._source_files(key, doc)
        available = {k for k, (f, _d) in files.items() if f}
        return tg.variants_view(doc, available, self._skip_set(report))

    def timeline_dry_run(self, tid: str, mode: plan.Mode = "auto", variant: str | None = None,
                         toggles: dict[str, bool] | None = None,
                         quality: plan.Quality = "best") -> dict[str, Any]:
        from .timeline import render as tl_render

        doc, key, report = self._timeline_ctx(tid)
        files = self._source_files(key, doc)
        corr = tl_render.corrected_map(report)
        skip = self._skip_set(report)
        off, tview, vlabel = self._toggle_state(doc, files, variant, toggles)
        show = tl_render.show_name(self._timeline_series_title(key, doc), doc["title"])
        show = tl_render.with_variant(show, vlabel)
        parts = []
        for pi, part in enumerate(doc["parts"]):
            cut, warns = tl_render.part_cut(doc, pi, files, corr, skip, off)
            if not cut.segments:
                parts.append({"part": pi + 1, "title": part["title"], "skipped": True,
                              "warnings": warns})
                continue
            amap, awarn = self._audio_map(list({s.file for s in cut.segments}))
            out = self._timeline_part_path(show, doc, pi, part["title"])
            rp = plan.build_plan(cut, self.cfg.ffmpeg, out, Path("<workdir>"), self.prober,
                                 mode=mode, audio_map=amap, quality=quality)
            parts.append({"part": pi + 1, "title": part["title"], "mode": rp.mode,
                          "reason": rp.reason, "output": rp.output,
                          "runtime": round(cut.final_runtime, 1),
                          "segments": [{**asdict(s), "duration": round(s.duration, 3)}
                                       for s in cut.segments],
                          "command": rp.command, "files": rp.files,
                          "warnings": warns + awarn + rp.warnings})
        return {"show": show, "synced": bool(report), "variant": variant or "",
                "toggles": tview,
                "runtime": round(sum(p.get("runtime", 0) for p in parts), 1),
                "layout": "movie" if len(doc["parts"]) == 1 else "show", "parts": parts}

    def _timeline_part_path(self, show: str, doc: dict[str, Any], pi: int,
                            part_title: str) -> Path:
        from .timeline import render as tl_render

        ext = self.cfg.ffmpeg.container
        if len(doc["parts"]) == 1:  # a single-part edit is a movie to Plex
            return self.cfg.output_dir / show / f"{show}.{ext}"
        return (self.cfg.output_dir / show / "Season 01"
                / tl_render.part_filename(show, pi + 1, part_title, ext))

    def submit_timeline_render(self, tid: str, mode: str = "auto", keep: bool | None = None,
                               refresh: bool = False,
                               require_sync: bool = True, variant: str | None = None,
                               toggles: dict[str, bool] | None = None,
                               collection: bool = True,
                               quality: plan.Quality = "best") -> dict[str, Any]:
        from .timeline import toggles as tg

        doc, _key, report = self._timeline_ctx(tid)
        if require_sync and not report:
            raise FansCutError("run sync first: POST /api/timelines/{id}/sync")
        tg.resolve(doc, set(), variant, toggles)  # unknown variant/toggle -> 400 now
        label = tg.variant_label(doc, variant)
        name = f"Render {doc['title']}" + (f" ({label})" if label else "")
        return self.worker.submit("timeline_render", name + (" · fast 1080p" if quality == "fast"
                                                             else ""),
                                  {"timeline_id": tid, "mode": mode, "keep": keep,
                                   "refresh": refresh, "variant": variant,
                                   "toggles": toggles, "collection": collection,
                                   "quality": quality})

    def _job_timeline_render(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        from .render import subs
        from .render.cache import render_hash
        from .timeline import render as tl_render

        tid = payload["timeline_id"]
        doc, key, report = self._timeline_ctx(tid)
        if not report:
            raise FansCutError("run sync first: POST /api/timelines/{id}/sync")
        keep = payload.get("keep")
        if keep is None:
            keep = self.cfg.render.keep
        episodes_files = self._source_files(key, doc)
        ctx.phase(1, 5, "Check files and cut points",
                  f"{len(episodes_files)} source{'s' if len(episodes_files) != 1 else ''}")
        bad, health = self._files_health([f for f, _d in episodes_files.values() if f])
        if bad:
            raise FansCutError("library check failed: " + "; ".join(
                f"{Path(b['file']).name}: {b['message']} ({b['fix']})" for b in bad))
        corr = tl_render.corrected_map(report)
        skip = self._skip_set(report)
        off, _tview, vlabel = self._toggle_state(doc, episodes_files, payload.get("variant"),
                                                 payload.get("toggles"))
        show = tl_render.show_name(self._timeline_series_title(key, doc), doc["title"])
        show = tl_render.with_variant(show, vlabel)
        movie_mode = len(doc["parts"]) == 1  # single-part edit -> Plex movie folder
        show_dir = self.cfg.output_dir / show
        show_dir.mkdir(parents=True, exist_ok=True)
        if movie_mode:
            (show_dir / f"{show}.nfo").write_text(tl_render.nfo_movie(show), encoding="utf-8")
        else:
            (show_dir / "tvshow.nfo").write_text(tl_render.nfo_show(show), encoding="utf-8")

        outputs, cached, sidecars = [], 0, 0
        stats: list[dict[str, Any]] = []  # wall time per built part, for estimates
        warnings = [f"{Path(h['file']).name}: {h['message']}" for h in health]
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tl-render-", dir=self.cfg.data_dir) as tmp:
            for pi, part in enumerate(doc["parts"]):
                cut, warns = tl_render.part_cut(doc, pi, episodes_files, corr, skip, off)
                warnings += warns
                if not cut.segments:
                    warnings.append(f"part {pi + 1} ({part['title']}): nothing to render")
                    continue
                amap, awarn = self._audio_map(list({s.file for s in cut.segments}))
                warnings += awarn
                out = self._timeline_part_path(show, doc, pi, part["title"])
                workdir = Path(tmp) / f"part{pi + 1}"
                workdir.mkdir()
                mode: plan.Mode = payload.get("mode", "auto") or "auto"
                quality: plan.Quality = "fast" if payload.get("quality") == "fast" else "best"
                rp = plan.build_plan(cut, self.cfg.ffmpeg, out, workdir, self.prober,
                                     mode=mode, audio_map=amap, hdr_params=self._hdr_params,
                                     quality=quality)
                # a fast build never stands in for a best one, nor the other way round
                h = render_hash(f"timeline:{tid}:{pi + 1}"
                                + (":fast" if quality == "fast" else ""), cut, rp.mode,
                                self.cfg.ffmpeg, rp.chapters)
                n_parts = len(doc["parts"])
                if not payload.get("refresh") and (hit := self.renders.hit(h)):
                    ctx.phase(3, 5, "Cut and join",
                              f"part {pi + 1} of {n_parts} · {part['title']} · reused, unchanged")
                    cached += 1
                    outputs.append(hit["path"])
                else:
                    ctx.progress(pi / n_parts, f"part {pi + 1}/{n_parts}: {part['title']}")
                    t0 = time.monotonic()
                    graded = self._through_effectcraft(cut, workdir, ctx, pi)
                    if graded is not cut:  # the hash stays the recipe's, not the temp files'
                        rp = plan.build_plan(graded, self.cfg.ffmpeg, out, workdir,
                                             self.prober, mode=mode, audio_map=amap,
                                             hdr_params=self._hdr_params, quality=quality)
                    ctx.phase(3, 5, "Cut and join", f"part {pi + 1} of {n_parts} · {part['title']}"
                              f" · {'copied' if rp.mode == 'copy' else 're-encoded'}")
                    run.execute(rp, workdir, ctx.part(pi, n_parts), ctx.cancel)
                    stats.append({"runtime": round(cut.final_runtime, 1), "mode": rp.mode,
                                  "seconds": round(time.monotonic() - t0, 1),
                                  "height": self._out_height(cut)})
                    self.renders.drop_path(str(out), except_hash=h)
                    self.renders.add(h, key, f"{doc['title']} · part {pi + 1}", str(out),
                                     rp.mode, bool(keep), job_id=ctx.job_id)
                    outputs.append(str(out))
                ctx.phase(4, 5, "Subtitles and poster", f"part {pi + 1} of {n_parts}")
                subs_written, subs_warn = subs.write_sidecars(
                    self.cfg.ffmpeg, cut.segments, Path(outputs[-1]), self.prober)
                warnings += subs_warn
                sidecars += len(subs_written)
                if not movie_mode:
                    nfo = tl_render.nfo_episode(part["title"], pi + 1)
                    out.with_suffix(".nfo").write_text(nfo, encoding="utf-8")
        poster = None
        if self.cfg.render.poster:
            poster = (self.posters.write_for_build(tid, show_dir)
                      or self._installed_poster(tid, show_dir)
                      or (self._write_poster(show_dir, outputs[0]) if outputs else None))
        ctx.phase(5, 5, "Add to Plex", "rescan and collection" if self.cfg.library.plex.url
                  else "no Plex set up: the files are in the output folder")
        plex_note = self._plex_finalize(show, outputs,
                                        collection=payload.get("collection", True), tid=tid)
        return {"show": show, "parts": len(outputs), "cached": cached, "outputs": outputs,
                "poster": poster, "subtitles": sidecars, "stats": stats,
                "warnings": warnings, "plex": plex_note,
                "message": f"wrote {len(outputs)} part(s) to {show_dir} ({cached} cached)"}

    def _installed_poster(self, tid: str, show_dir: Path) -> str | None:
        """The cut's own poster (downloaded at install) beats a frame grab."""
        import shutil

        row = self.db.one("SELECT poster FROM installs WHERE timeline_id = ?", (tid,))
        src = Path(row["poster"]) if row and row["poster"] else None
        if not src or not src.is_file():
            return None
        dst = show_dir / f"poster{src.suffix}"
        shutil.copyfile(src, dst)
        return str(dst)

    def _write_poster(self, show_dir: Path, first_output: str) -> str | None:
        """poster.jpg from the middle of the first part — the user's own footage."""
        poster = show_dir / "poster.jpg"
        tmp = poster.with_suffix(".tmp.jpg")
        try:
            dur = self.prober(first_output).get("duration") or 60
            ffmpeg.run_capture([self.cfg.ffmpeg.binary, "-v", "error", "-y",
                                "-ss", f"{float(dur) / 2:.1f}", "-i", first_output,
                                "-frames:v", "1", "-q:v", "3", str(tmp)])
            tmp.replace(poster)
            return str(poster)
        except Exception:  # noqa: BLE001 - a poster is a nicety, never fatal
            with contextlib.suppress(OSError):
                tmp.unlink()
            return None

    def _plex_finalize(self, show: str, outputs: list[str], collection: bool = True,
                       tid: str | None = None) -> str:
        """Settings → Add to Plex: rescan, then add the result to the FansCut collection.

        Plex scans in the background, so the new item is often not there yet; the
        collection step then says so, and "Open in Plex" finds it later."""
        rcfg = self.cfg.render
        note = self._plex_refresh() if rcfg.plex_refresh else "Plex refresh is off"
        name = rcfg.plex_collection.strip()
        if not (collection and name and self.cfg.library.plex.url
                and self.cfg.library.plex.token and outputs):
            return note
        try:
            item, section = self._plex_find(show)
            if not item:
                return f"{note}; “{show}” not found in Plex yet (rescan picks it up)"
            rk = str(item["ratingKey"])
            plex = self.library.plex()
            assert plex is not None
            cid = plex.collection_id(section, name, rk)
            plex.add_to_collection(cid, rk)  # Plex dedups
            if tid:
                self._remember_plex("cut", tid, rk, show)
            return f"{note}; added “{show}” to the {name} collection"
        except Exception as exc:  # noqa: BLE001 - collection add is a courtesy
            return f"{note}; collection step failed: {exc}"

    def _plex_sections(self) -> list[dict[str, Any]]:
        """The show and movie sections FansCut works with (all of them when none are set)."""
        plex = self.library.plex()
        assert plex is not None
        pc = self.cfg.library.plex
        out = []
        for s in plex.sections():
            want = set(pc.sections) if s["type"] == "show" else set(pc.movie_sections)
            if s["type"] in ("show", "movie") and (
                    not want or s["key"] in want or s["title"] in want):
                out.append(s)
        return out

    def _plex_find(self, title: str) -> tuple[dict[str, Any] | None, str]:
        plex = self.library.plex()
        assert plex is not None
        for s in self._plex_sections():
            item = plex.find_in_section(s["key"], title)
            if item:
                return item, s["key"]
        return None, ""

    def _plex_refresh(self) -> str:
        """Ask Plex to rescan after a render: show sections and movie sections, since a
        single-part cut lands in a movie library. Best effort."""
        if not (self.cfg.library.plex.url and self.cfg.library.plex.token):
            return "no Plex configured"
        try:
            plex = self.library.plex()
            assert plex is not None
            secs = self._plex_sections()
            for s in secs:
                plex.refresh_section(s["key"])
            return f"plex refreshed {len(secs)} section(s)"
        except Exception as exc:  # noqa: BLE001 - refresh is a courtesy, never fatal
            return f"plex refresh failed: {exc}"

    # -- Plex deep links and what FansCut put into Plex -------------------------------
    def _remember_plex(self, kind: str, ref: str, rating_key: str, title: str,
                       detail: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO plex_items (kind, ref, rating_key, title, detail,"
            " created_at) VALUES (?,?,?,?,?,?)",
            (kind, ref, rating_key, title, dumps(detail or {}),
             _dt.datetime.now().isoformat(timespec="seconds")))

    def plex_items(self, kind: str) -> list[dict[str, Any]]:
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in self.db.query(
            "SELECT * FROM plex_items WHERE kind = ? ORDER BY created_at DESC", (kind,))]

    def _plex_server(self) -> tuple[str, str]:
        """(Plex URL, server id) for Plex Web links on the user's own server."""
        pc = self.cfg.library.plex
        if not (pc.url and pc.token):
            raise FansCutError("Plex is not set up: add it in Settings")
        machine = getattr(self, "_plex_machine", None)
        if not machine:
            plex = self.library.plex()
            assert plex is not None
            machine = self._plex_machine = plex.identity()["machineIdentifier"]
        return pc.url.rstrip("/"), machine

    def _plex_web(self, rating_key: str) -> str:
        from urllib.parse import quote

        url, machine = self._plex_server()
        return (f"{url}/web/index.html#!/server/{machine}/details"
                f"?key={quote(f'/library/metadata/{rating_key}', safe='')}")

    def movie_plex_url(self, key: str) -> str:
        """Movie → Play: the film's page in Plex (any copy Plex knows)."""
        movies, _ = self.library.list_movies()
        m = next((x for x in movies if x.key == key), None) or self.library.get_movie(key)
        rk = m.plex_key or next((x.plex_key for x in movies if x.plex_key and (
            (m.tmdb_id and x.tmdb_id == m.tmdb_id) or (x.title, x.year) == (m.title, m.year))),
            None)
        if not rk:
            raise NotFoundError(f"{m.title} is not in Plex")
        return self._plex_web(rk)

    def cut_plex_url(self, tid: str) -> str:
        """My cuts → Open in Plex: the built cut's page, found by its folder name once
        Plex has scanned it, then remembered."""
        row = self.db.one("SELECT rating_key FROM plex_items WHERE kind='cut' AND ref=?", (tid,))
        if row:
            return self._plex_web(row["rating_key"])
        from .timeline import render as tl_render

        doc, key, _report = self._timeline_ctx(tid)
        show = tl_render.show_name(self._timeline_series_title(key, doc), doc["title"])
        names = [show]
        for v in doc.get("variants", []):  # a variant build is "<show> (<label>)"
            names.append(tl_render.with_variant(show, v["label"]))
        if not (self.cfg.library.plex.url and self.cfg.library.plex.token):
            raise FansCutError("Plex is not set up: add it in Settings")
        for name in names:
            item, _sec = self._plex_find(name)
            if item:
                self._remember_plex("cut", tid, str(item["ratingKey"]), name)
                return self._plex_web(str(item["ratingKey"]))
        raise NotFoundError(f"“{show}” is not in Plex yet: build it, then let Plex scan")

    def playlist_plex_url(self, pid: str) -> str:
        from urllib.parse import quote

        if not self.db.one("SELECT 1 FROM plex_items WHERE kind='playlist' AND rating_key=?",
                           (pid,)):
            raise NotFoundError("FansCut did not make that playlist")
        url, machine = self._plex_server()
        return (f"{url}/web/index.html#!/server/{machine}/playlist"
                f"?key={quote(f'/playlists/{pid}', safe='')}")

    # -- build estimates --------------------------------------------------------------
    # Output seconds made per wall second when nothing was measured on this machine yet.
    TYPICAL_SPEED = {"copy": 40.0, ("encode", "4k"): 0.4, ("encode", "hd"): 1.6,
                     ("encode", "sd"): 4.0}
    GPU_FACTOR = 5.0  # NVENC against libx264
    HEVC_FACTOR = 0.5  # x265 on the CPU against x264

    @staticmethod
    def _res_class(height: int) -> str:
        return "4k" if height >= 1600 else "hd" if height >= 700 else "sd"

    def _out_height(self, cut: Any) -> int:
        heights = []
        for s in cut.segments:
            try:
                heights.append(int((self.prober(s.file).get("video") or {}).get("height") or 0))
            except Exception:  # noqa: BLE001 - only for statistics
                continue
        return max(heights, default=0)

    def _measured_speed(self, mode: str, res: str) -> float | None:
        """Speed of the last builds on this machine with this mode and size: output
        seconds over wall seconds, summed, so long builds count more than tiny ones."""
        made = took = 0.0
        for r in self.db.query("SELECT result FROM jobs WHERE kind = 'timeline_render' AND"
                               " status = 'done' ORDER BY updated_at DESC LIMIT 30"):
            for st in (json.loads(r["result"]).get("stats") or []):
                if (st.get("mode") == mode and st.get("seconds", 0) > 0
                        and self._res_class(int(st.get("height") or 0)) == res):
                    made += float(st["runtime"])
                    took += float(st["seconds"])
        return made / took if took else None

    FAST_BITRATE = 8_000_000  # bits/s: 1080p H.264 at the default CRF, about

    def timeline_estimate(self, tid: str, variant: str | None = None) -> dict[str, Any]:
        """CutDetail's "Build on this PC" and Build's Best / Fast choice: how long, how big,
        and why. `fast` is None when it would make the same thing as `best`."""
        dry = self.timeline_dry_run(tid, "auto", variant)
        live = [p for p in dry["parts"] if not p.get("skipped")]
        files = {s["file"] for p in live for s in p["segments"] if s.get("file")}
        infos: dict[str, dict[str, Any]] = {}
        for f in files:
            try:
                infos[f] = self.prober(f)
            except Exception:  # noqa: BLE001 - an unreadable file shows up elsewhere
                continue
        videos = [i.get("video") or {} for i in infos.values()]
        height = max((int(v.get("height") or 0) for v in videos), default=0)
        res = self._res_class(height)
        hdr = any(v.get("hdr") not in (None, "", "SDR") for v in videos)
        codecs = sorted({str(v.get("codec")) for v in videos if v.get("codec")})
        fx = [s for p in live for s in p["segments"] if s.get("fx")]
        graded = [s for s in fx if (s["fx"].get("video") or {}).get("grade")
                  and not s["fx"].get("speed")]
        ec = bool(self.cfg.render.effectcraft_cli and self.cfg.render.effectcraft_grade)
        # the sources' own bitrate (bits per output second), weighted by what each part uses
        rate = {}
        for f, i in infos.items():
            try:
                rate[f] = Path(f).stat().st_size * 8 / max(float(i.get("duration") or 0), 1)
            except OSError:
                continue

        def option(fast: bool) -> dict[str, Any]:
            seconds, basis, size = 0.0, "measured", 0.0
            cls = "hd" if fast and res == "4k" else res
            for p in live:
                mode = "encode" if fast or p["mode"] != "copy" else "copy"
                speed = self._measured_speed(mode, cls)
                if speed is None:
                    basis = "typical"
                    if mode == "copy":
                        speed = self.TYPICAL_SPEED["copy"]
                    else:
                        speed = self.TYPICAL_SPEED[("encode", cls)]
                        if self.cfg.ffmpeg.nvenc:
                            speed *= self.GPU_FACTOR
                        elif not fast and (hdr or self.cfg.ffmpeg.video_codec == "hevc"):
                            speed *= self.HEVC_FACTOR
                seconds += p["runtime"] / speed + 10  # plus opening files and writing the part
                for sg in p["segments"]:
                    bps = (self.FAST_BITRATE if fast
                           else rate.get(sg.get("file"), self.FAST_BITRATE)
                           * (1.0 if mode == "copy" else 0.8))
                    size += sg.get("duration", 0) * bps / 8
            if ec and graded:
                seconds += sum(s["duration"] for s in graded) / 0.5
            return {"seconds": round(seconds), "bytes": round(size), "basis": basis,
                    "label": ("Quick build" if seconds < 120 else "Build" if seconds < 1800
                              else "Long build")}

        res_name = {"4k": "4K", "hd": "1080p", "sd": "SD"}[res] if height else "unknown size"
        reasons = [f"{res_name}{' HDR' if hdr else ''}"]
        if any(p["mode"] != "copy" for p in live):
            why = next((p["reason"] for p in live if p["mode"] != "copy"), "")
            reasons.append(f"re-encoded{': ' + why if why else ''}")
        else:
            reasons.append("copied as-is, no re-encode")
        if len(codecs) > 1:
            reasons.append(f"{len(codecs)} codecs ({', '.join(c.upper() for c in codecs)})")
        if fx:
            reasons.append(f"{len(fx)} effect segment{'s' if len(fx) != 1 else ''}"
                           + (" through EffectCraft" if ec and graded else ""))
        best = {**option(False), "name": f"Best · {res_name}{' HDR' if hdr else ''}"}
        fast = ({**option(True), "name": "Fast · 1080p"}
                if plan.fast_differs(list(infos.values())) else None)
        return {**{k: best[k] for k in ("seconds", "label", "basis")}, "reasons": reasons,
                "best": best, "fast": fast, "parts": len(live), "gpu": self.cfg.ffmpeg.nvenc,
                "runtime": dry["runtime"]}

    def set_timeline_options(self, tid: str, auto_rebuild: bool) -> dict[str, Any]:
        if self.db.one("SELECT 1 FROM timelines WHERE id = ?", (tid,)) is None:
            raise NotFoundError(f"timeline {tid} not found")
        self.db.execute("UPDATE timelines SET auto_rebuild = ? WHERE id = ?",
                        (int(bool(auto_rebuild)), tid))
        return {"auto_rebuild": bool(auto_rebuild)}

    # -- preview clips and stills (sync review, Build, the editor, Health) ---------
    def _episode_file(self, key: str, episode_id: str) -> str:
        ep = {e.id: e for e in self.library.episodes(key)}.get(episode_id)
        if not ep or not ep.file or not Path(ep.file).is_file():
            raise NotFoundError(f"{episode_id} has no file in this library")
        return ep.file

    def _movie_file(self, key: str) -> str:
        m = self.library.get_movie(key)
        if not m.file or not Path(m.file).is_file():
            raise NotFoundError(f"{m.title} has no file in this library")
        return m.file

    def _timeline_source_file(self, tid: str, src: str) -> str:
        doc, key, _report = self._timeline_ctx(tid)
        if src not in doc["sources"]:
            raise NotFoundError(f"{src} is not a source of this timeline")
        f, _d = self._source_files(key, {"sources": {src: doc["sources"][src]}})[src]
        if not f or not Path(f).is_file():
            raise NotFoundError(f"{src} has no file in this library")
        return f

    def _preview_path(self, file: str, what: str, ext: str) -> Path:
        import hashlib

        st = Path(file).stat()
        raw = f"{file}|{st.st_mtime}|{st.st_size}|{what}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self.cfg.data_dir / "previews" / f"{digest}.{ext}"

    def _clip(self, file: str, t: float) -> Path:
        """10 s around t (5 s each side) as a tiny mp4, cached in data_dir/previews."""
        out = self._preview_path(file, f"{t:.2f}", "mp4")
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.mp4")
            ffmpeg.run_capture(
                [self.cfg.ffmpeg.binary, "-v", "error", "-ss", f"{max(t - 5, 0):.3f}", "-t", "10",
                 "-i", file, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                 "-vf", "scale=640:-2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                 "-movflags", "+faststart", str(tmp)])
            tmp.replace(out)
        return out

    def _frame(self, file: str, t: float, crop: str | None = None) -> Path:
        """One frame at t as a 640 px jpeg; `crop` (w:h:x:y) is drawn on it as a box."""
        if crop is not None and not CROP_RE.match(crop):
            raise FansCutError("crop must be w:h:x:y in pixels")
        info = self.prober(file)
        duration = info.get("duration") or 0
        if duration and t >= duration:
            raise NotFoundError(f"no frame at {t:.1f} s: the file is {duration:.1f} s long")
        hdr = (info.get("video") or {}).get("hdr") not in (None, "", "SDR")
        out = self._preview_path(file, f"frame|{t:.3f}|{crop or ''}|tm{int(hdr)}", "jpg")
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.jpg")
            vf = "scale=640:-2"
            if crop:
                w, h, x, y = crop.split(":")
                vf = f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x5B9BFF@0.9:t=6,{vf}"

            def grab(chain: str) -> None:
                ffmpeg.run_capture(
                    [self.cfg.ffmpeg.binary, "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", file,
                     "-frames:v", "1", "-vf", chain, "-q:v", "4", "-y", str(tmp)])

            if hdr:  # an HDR frame shown as is looks grey and flat: tone-map it to SDR
                try:
                    grab(f"{HDR_TO_SDR},{vf}")
                except FFmpegError:  # an ffmpeg without zscale: the plain frame
                    grab(vf)
            else:
                grab(vf)
            if not tmp.is_file():  # past the end: ffmpeg exits 0 and writes nothing
                raise NotFoundError("no frame at that time")
            tmp.replace(out)
        return out

    def preview_clip(self, key: str, episode_id: str, t: float) -> Path:
        return self._clip(self._episode_file(key, episode_id), t)

    def episode_frame(self, key: str, episode_id: str, t: float) -> Path:
        return self._frame(self._episode_file(key, episode_id), t)

    def movie_preview(self, key: str, t: float) -> Path:
        return self._clip(self._movie_file(key), t)

    def movie_frame(self, key: str, t: float) -> Path:
        return self._frame(self._movie_file(key), t)

    def timeline_source_preview(self, tid: str, src: str, t: float) -> Path:
        """Build and the editor preview any source, episode or film, the same way."""
        return self._clip(self._timeline_source_file(tid, src), t)

    def timeline_source_frame(self, tid: str, src: str, t: float) -> Path:
        return self._frame(self._timeline_source_file(tid, src), t)

    def check_frame(self, file: str) -> Path:
        """Health's "Preview crop": a frame of a file the last check flagged, with the
        detected picture area drawn. Only files named in that report are served."""
        last = self.last_check() or {}
        flagged = {f.get("file") for f in last.get("findings", [])}
        for f in last.get("findings", []):
            flagged.update(f.get("files") or [])
        if not file or file not in flagged or not Path(file).is_file():
            raise NotFoundError("that file is not in the last library check")
        info = self.prober(file)
        duration = info.get("duration") or 0
        crop = self.checker.crop(file, duration)
        return self._frame(file, duration * 0.3 + 2.0, crop)  # inside the cropdetect window

    # -- find a scene in other episodes --------------------------------------------
    def submit_find_similar(self, key: str, episode_id: str, start: float, end: float
                            ) -> dict[str, Any]:
        episodes = {e.id: e for e in self.library.episodes(key)}
        ep = episodes.get(episode_id)
        if not ep or not ep.file:
            raise NotFoundError(f"{episode_id} has no file in this library")
        if end - start < 2:
            raise FansCutError("the range must be at least 2 seconds")
        return self.worker.submit("find_similar", f"Find {episode_id} {start:.0f}s scene",
                                  {"series_key": key, "episode_id": episode_id,
                                   "start": start, "end": end})

    def _job_find_similar(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        """Locate a range's audio in every other episode (flashbacks, reused footage)."""
        from .fingerprint.audio import features
        from .fingerprint.match import find_shared

        key, eid = payload["series_key"], payload["episode_id"]
        fp, ff = self.cfg.fingerprint, self.cfg.ffmpeg
        episodes = [e for e in self.library.episodes(key) if e.file and e.duration]
        src = next(e for e in episodes if e.id == payload["episode_id"])
        assert src.file is not None
        query = features(ffmpeg.decode_audio(ff, src.file, payload["start"],
                                             payload["end"] - payload["start"], fp.sample_rate),
                         fp.sample_rate)
        min_len = max((payload["end"] - payload["start"]) * 0.8, 5.0)
        max_len = (payload["end"] - payload["start"]) * 2
        hits = []
        for i, e in enumerate(episodes):
            ctx.progress(i / len(episodes), e.id)
            if e.id == src.id:
                continue
            assert e.file is not None and e.duration is not None
            hay = features(ffmpeg.decode_audio(ff, e.file, 0, e.duration, fp.sample_rate),
                           fp.sample_rate)
            for m in find_shared(query, hay, 0.7, min_len, max_len):
                hits.append({"episode_id": e.id, "start": round(m.b_start, 2),
                             "end": round(m.b_end, 2)})
        hits.sort(key=lambda h: (h["episode_id"], h["start"]))
        return {"matches": hits,
                "message": f"{len(hits)} possible repeat(s) of {eid} "
                           f"{payload['start']:.0f}–{payload['end']:.0f}s"}

    # -- library check --------------------------------------------------------
    @property
    def checker(self) -> Any:
        from .library.check import LibraryChecker

        return LibraryChecker(self.cfg, self.db, self.prober, self.library)

    def _all_timeline_sources(self) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        out = []
        for r in self.db.query("SELECT series_key, title, doc FROM timelines"):
            doc = json.loads(r["doc"])
            try:
                files = self._source_files(r["series_key"], doc)
            except Exception:  # noqa: BLE001 - a broken timeline must not stop the check
                continue
            out.append((r["title"], doc, {k: v[0] for k, v in files.items()}))
        return out

    def library_check(self, q: str = "", deep: bool = False) -> dict[str, Any]:
        from .library.check import READ_ONLY_HINT, folder_writable

        result: dict[str, Any] = self.checker.run(q=q, deep=deep,
                                                  timelines=self._all_timeline_sources())
        for r in result.get("renames", []):
            r["writable"] = folder_writable(r["file"])
        if any(not r["writable"] for r in result.get("renames", [])):
            result["rename_blocked"] = READ_ONLY_HINT
        if not q:  # a whole-library run is what the nav badge and library notes show
            result["checked_at"] = _dt.datetime.now().isoformat(timespec="seconds")
            self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(self.cfg.data_dir / "library-check.json", dumps(result))
        return result

    def last_check(self) -> dict[str, Any] | None:
        """The last whole-library check, or None if it never ran."""
        f = self.cfg.data_dir / "library-check.json"
        try:
            data: dict[str, Any] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data

    def rename_file(self, file: str, to: str) -> dict[str, str]:
        from .library.check import apply_rename

        proposals = self.checker.run(q="", timelines=[])["renames"]
        try:
            done = apply_rename(proposals, file, to)
        except (ValueError, OSError) as exc:
            raise FansCutError(str(exc)) from exc
        self.library.refresh()
        return done

    def _files_health(self, files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(errors, warnings) of the per-file check for the files a render will read."""
        errors, warns = [], []
        movies, _ = self.library.list_movies()
        by_file = {m.file: m for m in movies if m.file}
        for f in sorted(set(files)):
            if not Path(f).is_file():
                continue  # unreachable files are reported by the source resolution itself
            mv = by_file.get(f)
            found, _ed, _info = self.checker.check_file(
                f, mv.title if mv else Path(f).stem, mv.year if mv else None,
                mv.tmdb_id if mv else None)
            errors += [x for x in found if x["severity"] == "error"]
            warns += [x for x in found if x["severity"] == "warn"]
        return errors, warns

    # -- community catalogue ------------------------------------------------------
    def _github(self) -> Any:
        from .community import GitHub

        return GitHub(self.cfg.community.github_token, self.session)

    def _index(self) -> list[dict[str, Any]]:
        from . import community

        if self.cfg.community.offline or not self.cfg.community.index_url:
            return []
        cached = getattr(self, "_index_cache", None)
        now = _dt.datetime.now().timestamp()
        if cached and now - cached[0] < 600:
            return list(cached[1])
        url = self.cfg.community.index_url
        try:
            local = community.local_index_path(url)
            if local is not None:  # a draft index on disk
                if local.stat().st_size > community.MAX_INDEX:
                    raise community.CommunityError("index.json too large")
                entries = community.validate_index(json.loads(local.read_text(encoding="utf-8")))
            else:
                r = (self.session or requests).get(url, timeout=20,
                                                   headers={"User-Agent": "FansCut"})
                r.raise_for_status()
                if len(r.content) > community.MAX_INDEX:
                    raise community.CommunityError("index.json too large")
                entries = community.validate_index(r.json())
        except (requests.RequestException, ValueError, OSError, community.CommunityError):
            entries = cached[1] if cached else []
        self._index_cache = (now, entries)
        return list(entries)

    def catalogue(self) -> dict[str, Any]:
        """Every cut in the community index, rated against this library."""
        from . import community, suggest

        movies, _ = self.library.list_movies()
        series, _ = self.library.list_series()
        tmdb = {m.tmdb_id for m in movies if m.tmdb_id and m.file}
        genres_of = {m.tmdb_id: m.genres for m in movies if m.tmdb_id and m.genres}
        genres_of_tv = {s.tvdb_id: s.genres for s in series if s.tvdb_id and s.genres}
        tvdb = {s.tvdb_id for s in series if s.tvdb_id}
        installed = {r["repo"]: r["timeline_id"] for r in self.db.query(
            "SELECT repo, timeline_id FROM installs")}
        out = []
        for e in self._index():
            status, missing = community.availability(e, tmdb, tvdb)
            out.append({"id": f"repo:{e['slug']}", "kind": "community", "poster_kind": e["kind"],
                        "title": e["name"], "category": e["category"], "tagline": e["tagline"],
                        "status": status, "missing": missing, "repo": e["repo"],
                        "poster": e["poster"], "installed": installed.get(e["slug"]),
                        "tmdb_ids": [x["tmdb"] for x in e["sources"] if x.get("tmdb")],
                        "genres": suggest.rank_genres(
                            [genres_of.get(x.get("tmdb"), []) for x in e["sources"]]
                            + [genres_of_tv.get(x.get("tvdb"), []) for x in e["sources"]]),
                        "season": suggest.season_for(
                            e["name"], [x["title"] for x in e["sources"]], e.get("season")),
                        "tvdb_ids": [x["tvdb"] for x in e["sources"] if x.get("tvdb")],
                        "detail": {"sources": len(e["sources"]), "variants": e["variants"]}})
        return {"index": self.cfg.community.index_url, "cuts": out}

    def install_cut(self, url: str, ref: str | None = None) -> dict[str, Any]:
        """Fetch fanscut.json from a cut repo at its branch head and install or update it."""
        from . import community
        from .timeline import recipe as tl_recipe

        repo = community.parse_repo(url)
        gh = self._github()
        branch, sha = gh.head(repo, ref)
        try:
            doc = json.loads(gh.raw(repo, sha, "fanscut.json"))
        except ValueError as exc:
            raise community.CommunityError("fanscut.json is not valid JSON") from exc
        doc = self._fill_movie_durations(doc)
        clean = tl_recipe.validate(doc)
        row = self.db.one("SELECT * FROM installs WHERE repo = ?", (repo.slug,))
        now = _dt.datetime.now().isoformat(timespec="seconds")
        summary = ""
        old = self.db.one("SELECT doc FROM timelines WHERE id = ?",
                          (row["timeline_id"],)) if row else None
        if row and old:
            tid = row["timeline_id"]
            changed = row["sha"] != sha
            if changed:
                summary = community.change_summary(json.loads(old["doc"]), clean)
                self.update_timeline(tid, clean)
        else:
            tid = self.create_timeline(self._timeline_key(clean), clean)["id"]
            changed = True
        poster = self._fetch_poster(repo.slug, clean.get("poster") or next(
            (e["poster"] for e in self._index() if e["slug"] == repo.slug), ""), tid)
        self.db.execute(
            "INSERT OR REPLACE INTO installs (timeline_id, repo, ref, sha, poster, installed_at,"
            " updated_at) VALUES (?,?,?,?,?,COALESCE((SELECT installed_at FROM installs WHERE"
            " timeline_id = ?), ?),?)",
            (tid, repo.slug, branch, sha, poster or (row["poster"] if row else ""), tid, now, now))
        rebuild: list[str] = []
        opt = self.db.one("SELECT auto_rebuild FROM timelines WHERE id = ?", (tid,))
        if summary and opt and opt["auto_rebuild"]:  # an update to a cut set to rebuild itself
            sync = self.submit_timeline_sync(tid)
            render = self.submit_timeline_render(tid, require_sync=False)  # sync runs first
            rebuild = [sync["id"], render["id"]]
        return {"id": tid, "title": clean["title"], "repo": repo.url, "ref": branch, "sha": sha,
                "changed": changed, "poster": bool(poster), "summary": summary,
                "rebuild": rebuild}

    def _fill_movie_durations(self, doc: Any) -> Any:
        """Movie sources may omit duration; the user's copy supplies it."""
        if not isinstance(doc, dict) or not isinstance(doc.get("sources"), dict):
            return doc
        for s in doc["sources"].values():
            if isinstance(s, dict) and s.get("kind") == "movie" and not s.get("duration"):
                m = self.library.find_movie(s.get("tmdb"), edition=s.get("edition") or "")
                if m and m.duration:
                    s["duration"] = round(m.duration, 3)
        return doc

    def _timeline_key(self, doc: dict[str, Any]) -> str:
        series_list, _ = self.library.list_series()
        tvdb = doc["series"].get("tvdb_id")
        match = next((s for s in series_list if tvdb and s.tvdb_id == tvdb), None) or next(
            (s for s in series_list if s.title.lower() == doc["series"]["title"].lower()), None)
        if match:
            return match.key
        movie = next((s for s in doc["sources"].values() if s.get("kind") == "movie"), None)
        if movie:
            return f"movie:{movie['tmdb']}"
        raise FansCutError("no series or movie from this cut is in the library")

    def _fetch_poster(self, slug: str, url: str, tid: str) -> str:
        from . import community

        if not url:
            return ""
        try:
            data, ext = self._github().poster(url)
        except community.CommunityError:
            return ""  # a poster is a nicety; the install still counts
        return community.save_poster(data, ext, self.cfg.data_dir / "posters", tid)

    def cut_updates(self) -> dict[str, Any]:
        """Installed cuts whose repo moved on since install."""
        from . import community

        out = []
        gh = self._github()
        for r in self.db.query("SELECT * FROM installs"):
            try:
                _branch, sha = gh.head(community.Repo(*r["repo"].split("/", 1)), r["ref"])
                err = ""
            except community.CommunityError as exc:
                sha, err = r["sha"], str(exc)
            summary = ""
            if sha != r["sha"] and not err:
                summary = self._update_summary(gh, r["repo"], sha, r["timeline_id"])
            out.append({"id": r["timeline_id"], "repo": f"https://github.com/{r['repo']}",
                        "ref": r["ref"], "installed": r["sha"], "latest": sha,
                        "update": sha != r["sha"], "error": err, "summary": summary})
        return {"installs": out}

    def _update_summary(self, gh: Any, slug: str, sha: str, tid: str) -> str:
        """"2 episodes re-timed": the new recipe against the installed one. "" if unknown."""
        from . import community

        try:
            new = json.loads(gh.raw(community.Repo(*slug.split("/", 1)), sha, "fanscut.json"))
            row = self.db.one("SELECT doc FROM timelines WHERE id = ?", (tid,))
            return community.change_summary(json.loads(row["doc"]), new) if row else ""
        except (community.CommunityError, ValueError, TypeError, KeyError):
            return ""

    def update_cut(self, tid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM installs WHERE timeline_id = ?", (tid,))
        if row is None:
            raise NotFoundError(f"{tid} was not installed from a repo")
        return self.install_cut(f"https://github.com/{row['repo']}", row["ref"])

    # -- templates ----------------------------------------------------------------
    def template_doc(self, tid: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """(recipe, timeline key) a template produces from the user's own library."""
        from . import templates
        from .timeline import recipe as tl_recipe

        t = templates.get(tid)
        title = str(params.get("title") or "").strip()[:tl_recipe.MAX_TITLE]
        author = str(params.get("author") or "").strip()[:100]
        if t["needs"] == "movies":
            movies, _ = self.library.list_movies()
            by_key = {m.key: m for m in movies}
            films = []
            for k in params.get("movies") or []:
                m = by_key.get(k)
                if m is None:
                    raise NotFoundError(f"movie {k} not found")
                if not m.tmdb_id:
                    raise FansCutError(f"{m.title}: no TMDB id (add {{tmdb-...}} to its folder)")
                dur = m.duration or (self.prober(m.file).get("duration") if m.file else None)
                films.append({"tmdb": m.tmdb_id, "title": m.title, "year": m.year,
                              "duration": dur, "edition": self._movie_edition(m)})
            name = title or " + ".join(str(f["title"]) for f in films)[:200]
            doc = templates.movie_doc(t, name, films, author)
            key = f"movie:{films[0]['tmdb']}"
        else:
            key = str(params.get("series_key") or "")
            s = self.library.get_series(key)
            view = self.episode_view(key)
            eps = [e for e in view["episodes"] if e.get("has_file")]
            doc = templates.series_doc(t, title or f"{s.title} - {t['label']}", asdict(s),
                                       eps, author)
        return tl_recipe.validate(doc), key

    def _movie_edition(self, m: Any) -> str | None:
        from .library.check import edition_from_name

        return edition_from_name(Path(m.file).name) if m.file else None

    def template_preview(self, tid: str, params: dict[str, Any]) -> dict[str, Any]:
        from . import templates

        doc, _key = self.template_doc(tid, params)
        return {"files": templates.files(templates.get(tid), doc)}

    def template_create(self, tid: str, params: dict[str, Any]) -> dict[str, Any]:
        """Write the repo files locally, add the timeline, optionally push a private repo."""
        from . import templates

        t = templates.get(tid)
        doc, key = self.template_doc(tid, params)
        out_files = templates.files(t, doc)
        folder = self.cfg.data_dir / "cuts" / templates.slug(doc["title"])
        folder.mkdir(parents=True, exist_ok=True)
        for name, text in out_files.items():
            (folder / name).write_text(text, encoding="utf-8")
        new_id = self.create_timeline(key, doc)["id"]
        result: dict[str, Any] = {"id": new_id, "title": doc["title"], "folder": str(folder),
                                  "files": sorted(out_files)}
        push = params.get("push") or {}
        if push.get("name"):
            gh = self._github()
            repo = gh.create_repo(templates.slug(str(push["name"])),
                                  bool(push.get("private", True)), f"FansCut cut: {doc['title']}")
            sha = ""
            for fname in ("fanscut.json", "README.md", "outline.md"):
                sha = gh.put_file(repo, fname, out_files[fname], f"Add {fname}") or sha
            now = _dt.datetime.now().isoformat(timespec="seconds")
            self.db.execute(
                "INSERT OR REPLACE INTO installs (timeline_id, repo, ref, sha, poster,"
                " installed_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (new_id, repo.slug, "main", sha or "0" * 40, "", now, now))
            result["repo"] = repo.url
        return result

    # -- first-run suggestions ----------------------------------------------------
    def _lang_ok(self, path: str | None) -> str | None:
        """None if fine, else the reason this file fails the preferred-language check."""
        lang = self.cfg.render.language
        if not lang or not path:
            return None
        try:
            tracks = self.prober(path).get("audio_tracks") or []
        except OSError:
            return None  # file not reachable; the missing-file check reports it instead
        if tracks and not any((t.get("language") or "").lower() == lang.lower()
                              for t in tracks):
            return f"no '{lang}' audio track"
        return None

    def suggestions(self) -> dict[str, Any]:
        from . import suggest
        from .timeline import recipe as tl_recipe

        out: list[dict[str, Any]] = []
        series_list, _ = self.library.list_series()
        movies, _ = self.library.list_movies()
        by_tvdb = {s.tvdb_id: s for s in series_list if s.tvdb_id}

        # (a) community recipes dropped into <data_dir>/recipes
        rdir = self.cfg.data_dir / "recipes"
        for f in sorted(rdir.glob("*.json")) if rdir.is_dir() else []:
            try:
                doc = tl_recipe.validate(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001 - a bad file must not hide the rest
                out.append({"id": f"recipe:{f.name}", "kind": "recipe", "title": f.name,
                            "status": "missing",
                            "missing": [{"src": "-", "reason": f"invalid recipe: {exc}"}],
                            "keys": [], "detail": {}})
                continue

            art: list[str] = []
            keys: list[str] = []

            def resolve(src: str, entry: dict[str, Any], _doc: dict = doc,
                        _art: list[str] = art, _keys: list[str] = keys,
                        ) -> tuple[str | None, float | None, str | None]:
                if entry.get("kind") == "movie":
                    m = self.library.find_movie(entry.get("tmdb"),
                                                edition=entry.get("edition", ""))
                    if m:
                        _art.append(art_ref(m))
                        _keys.append(m.key)
                    if not m or not m.file:
                        ed = f" ({entry.get('edition')})" if entry.get("edition") else ""
                        return None, None, f"movie {entry.get('tmdb')}{ed} not in the library"
                    bad, _w = self._files_health([m.file])
                    if bad:
                        return None, None, f"{bad[0]['message']} — {bad[0]['fix']}"
                    return m.file, m.duration, self._lang_ok(m.file)
                tvdb = (_doc.get("series") or {}).get("tvdb_id")
                s = by_tvdb.get(tvdb) if tvdb else None
                if not s:
                    titles = [x for x in series_list
                              if x.title.lower() == (_doc["series"].get("title") or "").lower()]
                    s = titles[0] if titles else None
                if not s:
                    stitle = (_doc.get("series") or {}).get("title")
                    return None, None, f"series {stitle!r} not in the library"
                _art.append(art_ref(s))
                _keys.append(s.key)
                eps = {e.id: e for e in self.library.episodes(s.key)}
                e = eps.get(src)
                if not e or not e.file:
                    return None, None, f"{src} has no file"
                return e.file, e.duration, self._lang_ok(e.file)

            out.append(suggest.from_recipe(f.name, doc, resolve, art, keys))

        # (b) Plex collections the user partly or fully owns
        from .browse import collection_groups

        # Plex tags plus known sets (The Hobbit, …); one copy per film
        for g in collection_groups(movies):
            ms = g["movies"]
            out += suggest.from_collection(
                g["name"], ms, [m.title for m in ms if m.file and self._lang_ok(m.file)], g["id"])

        # (c) canon cuts for matched shows with known filler
        for row in self.db.query("SELECT * FROM matches WHERE season = -1"):
            show = self.fillers.find(row["mal_id"], row["anilist_id"])
            if not show:
                continue
            s = next((x for x in series_list if x.key == row["series_key"]), None)
            if not s:
                continue
            eps = self.library.episodes(s.key)
            n_filler = sum(1 for v in show.labels.values() if v == "filler")
            missing_files = [e.id for e in eps if not e.file]
            lang_missing = [e.id for e in eps if e.file and self._lang_ok(e.file)]
            sgn = suggest.canon_cut(s.title, s.key, n_filler, len(eps),
                                    missing_files, lang_missing, art_ref(s))
            if sgn:
                out.append(sgn)
        # genres and season from the library items each suggestion uses (Discover shelves)
        items: dict[str, Any] = {m.key: m for m in movies}
        items.update({s.key: s for s in series_list})
        for sg in out:
            used = [items[k] for k in sg.get("keys", []) if k in items]
            sg["genres"] = suggest.rank_genres([x.genres for x in used])
            sg["season"] = suggest.season_for(sg.get("name") or sg.get("title") or "",
                                              [x.title for x in used])
        order = {"ready": 0, "partly": 1, "missing": 2}
        out.sort(key=lambda s: (order.get(s["status"], 3), s["title"]))
        return {"suggestions": out}

    def install_suggestion(self, sid: str) -> dict[str, Any]:
        kind, _, ref = sid.partition(":")
        if kind == "repo":  # a community catalogue entry: owner/name
            done = self.install_cut(f"https://github.com/{ref}")
            job = self.submit_timeline_sync(done["id"])
            return {"action": "install", "timeline": done["id"], "jobs": [job["id"]],
                    "sha": done["sha"]}
        if kind == "recipe":
            from .timeline import recipe as tl_recipe

            f = self.cfg.data_dir / "recipes" / ref
            if not f.is_file() or f.parent.resolve() != (self.cfg.data_dir / "recipes").resolve():
                raise NotFoundError(f"recipe file {ref} not found")
            doc = tl_recipe.validate(json.loads(f.read_text(encoding="utf-8")))
            series_list, _ = self.library.list_series()
            tvdb = (doc.get("series") or {}).get("tvdb_id")
            match = next((s for s in series_list if tvdb and s.tvdb_id == tvdb), None)
            if not match:
                title = (doc.get("series") or {}).get("title", "").lower()
                match = next((s for s in series_list if s.title.lower() == title), None)
            first_movie = next((s for s in doc["sources"].values()
                                if s.get("kind") == "movie"), None)
            if not match and not first_movie:
                raise FansCutError("no series or movie from this recipe is in the library")
            key = match.key if match else f"movie:{(first_movie or {}).get('tmdb')}"
            tid = self.create_timeline(key, doc)["id"]
            sync = self.submit_timeline_sync(tid)
            render = self.submit_timeline_render(tid, require_sync=False)  # sync runs first
            return {"action": "render", "timeline": tid,
                    "jobs": [sync["id"], render["id"]]}
        if kind == "merge":
            name, movies = self._collection_movies(ref)
            if len(movies) < 2:
                raise FansCutError(f"collection {ref!r} has fewer than two owned movies")
            doc = self._merge_doc(name, movies)
            tid = self.create_timeline(f"movie:{movies[0].tmdb_id}", doc)["id"]
            sync = self.submit_timeline_sync(tid)
            render = self.submit_timeline_render(tid, require_sync=False)  # sync runs first
            return {"action": "render", "timeline": tid,
                    "jobs": [sync["id"], render["id"]]}
        if kind == "order":
            name, movies = self._collection_movies(ref)
            keys = [m.plex_key for m in movies if m.plex_key]
            if not keys:
                raise FansCutError("watch-order playlists need the movies in Plex")
            plex = self.library.plex()
            if not plex:
                raise FansCutError("Plex is not configured")
            title = f"{name} — watch order"
            pid = plex.create_playlist(title, keys)
            self._remember_plex("playlist", sid, pid, title,
                                {"films": [m.title for m in movies],
                                 "keys": [m.key for m in movies]})
            return {"action": "playlist", "playlist_id": pid}
        if kind == "canon":
            job = self.submit_render(ref, None, {"preset": "canon_only"}, "auto")
            return {"action": "render", "jobs": [job["id"]]}
        raise NotFoundError(f"unknown suggestion {sid!r}")

    def _collection_movies(self, ref: str) -> tuple[str, list[Any]]:
        """(name, one copy per film) for a collection id ("tmdb-119") or name."""
        from .browse import find_group

        movies, _ = self.library.list_movies()
        g = find_group(movies, ref)
        if g is None:
            raise NotFoundError(f"collection {ref!r} not found")
        return g["name"], g["movies"]

    def _merge_doc(self, name: str, movies: list[Any]) -> dict[str, Any]:
        sources, segments = {}, []
        for m in movies:
            if not m.file:
                continue
            dur = m.duration or self.prober(m.file).get("duration") or 0
            src = f"M{m.tmdb_id}"
            sources[src] = {"kind": "movie", "tmdb": m.tmdb_id, "duration": dur,
                            "note": m.title}
            segments.append({"src": src, "start": 0.0, "end": round(float(dur), 3)})
        return {"schema": 2, "kind": "timeline", "title": f"{name} — merged",
                "series": {"title": name}, "sources": sources,
                "parts": [{"title": name, "segments": segments}],
                "credits": {"author": "FansCut template", "based_on": "Plex collection"}}

    # -- AI-assisted recipes ----------------------------------------------------
    def ai_recipe(self, key: str, notes: str, apply: bool = False) -> dict[str, Any]:
        """Community cut notes -> validated recipe (schema 2); optionally applied."""
        series = self.library.get_series(key)
        doc = ai_mod.generate_recipe(self.cfg.ai, series, self.library.episodes(key), notes)
        out: dict[str, Any] = {"recipe": doc}
        if apply:
            out["import"] = self.import_recipe(key, doc)
        return out

    def ai_outline(self, key: str, notes: str) -> dict[str, Any]:
        """Chronology notes -> scene outline (parts with placeholder segments)."""
        series = self.library.get_series(key)
        outline = ai_mod.generate_outline(self.cfg.ai, series, self.library.episodes(key), notes)
        known = {e.id for e in self.library.episodes(key)}
        for part in outline:
            part["scenes"] = [s for s in part["scenes"] if s["src"] in known]
        return {"outline": [p for p in outline if p["scenes"]]}

    # -- recipes --------------------------------------------------------------
    def export_recipe(self, key: str) -> dict[str, Any]:
        series = self.library.get_series(key)
        m, _ = self.match_for(key, WHOLE)
        # Detected and hand-edited ranges both go in; a cleared one exports as null.
        trims = {eid: {k: None if v["start"] is None else (v["start"], v["end"])
                       for k, v in t.items()} for eid, t in self.trims(key).items()}
        return recipe_mod.build(series.title, m["mal_id"] if m else None,
                                m["anilist_id"] if m else None, self.settings(key),
                                self.overrides(key), trims, self.custom_segments(key))

    def import_recipe(self, key: str, doc: Any) -> dict[str, Any]:
        clean = recipe_mod.validate(doc)
        episodes = {e.id for e in self.library.episodes(key)}
        applied, skipped = 0, []
        for eid, e in clean["episodes"].items():
            if eid not in episodes:
                skipped.append(eid)
                continue
            applied += 1
            if "label" in e or "include" in e:
                self.set_override(key, eid, e.get("label", UNSET), e.get("include", UNSET))
            if "segments" in e:
                self.set_segments(key, eid, e["segments"])
            for kind, rng in e.get("trims", {}).items():
                self.set_trim(key, eid, kind, *(rng if rng else (None, None)))
        changes = dict(clean["options"])
        if clean["preset"]:
            changes["preset"] = clean["preset"]
        if changes:
            self.save_settings(key, changes)
        s = clean["series"]
        if (s.get("mal_id") or s.get("anilist_id")) and not self.matches(key):
            self.set_match(key, s.get("mal_id"), s.get("anilist_id"), s.get("title") or "")
        return {"applied": applied, "skipped": sorted(skipped)}

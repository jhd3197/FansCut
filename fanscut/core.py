"""The app's brain: ties config, SQLite state, library, catalogues, labels, planner and renderer.

Routes stay thin and call into this; so do the job handlers.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import itertools
import json
import tempfile
import uuid
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import requests

from . import ai as ai_mod
from . import recipe as recipe_mod
from .catalogues import match as catalogue_match
from .catalogues.http import CachedHttp
from .config import Config
from .db import Database, dumps
from .errors import FansCutError, NotFoundError
from .fillers import anifiller, jikan_flags
from .fillers import labels as labelling
from .fingerprint import detect
from .fingerprint.cache import FingerprintCache
from .jobs import JobContext, JobStore, Worker
from .library.service import Library
from .models import LABELS, TRIM_KINDS, Episode
from .planner import PRESETS, CutOptions, CutPlan, PlanEpisode, plan_cut
from .render import ffmpeg, plan, run
from .render.cache import RenderStore, render_hash

UNSET: Any = object()  # "leave this field alone", distinct from None (= clear it)
WHOLE = -1  # matches.season value for "the whole series"


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


class Core:
    def __init__(self, cfg: Config, db: Database, session: requests.Session | None = None):
        self.db = db
        self.session = session
        self.jobs = JobStore(db)
        self.renders = RenderStore(db)
        self.worker = Worker(self.jobs, {"render": self._job_render, "detect": self._job_detect,
                                         "timeline_sync": self._job_timeline_sync,
                                         "timeline_render": self._job_timeline_render,
                                         "find_similar": self._job_find_similar})
        self.apply_config(cfg)

    def apply_config(self, cfg: Config) -> None:
        """(Re)build everything that depends on settings; at start and after a settings save."""
        self.cfg = cfg
        self.prober = ffmpeg.Prober(cfg.ffmpeg, self.db)
        self.library = Library(cfg, self.prober)
        cat = cfg.catalogues
        self.http_jikan = CachedHttp(self.db, 0.4, cat.cache_days, cat.offline, self.session)
        self.http_anilist = CachedHttp(self.db, 0.8, cat.cache_days, cat.offline, self.session)
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
        rp = plan.build_plan(cut, self.cfg.ffmpeg, provisional, workdir, self.prober, titles, mode)
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
                      mode: str, keep: bool | None = None, refresh: bool = False) -> dict[str, Any]:
        series = self.library.get_series(key)
        self.build_cut(key, seasons, options)  # fail fast on a bad request
        return self.worker.submit("render", f"Render {series.title}",
                                  {"series_key": key, "seasons": seasons, "options": options,
                                   "mode": mode, "keep": keep, "refresh": refresh,
                                   "title": series.title})

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
                                           keep=self.cfg.automation.keep))
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
        return {"episodes": len(eps), "message": f"analysed {len(eps)} episodes",
                "found": {k: {t: list(r) for t, r in v.items()} for k, v in found.items()}}

    def store_detected(self, key: str, found: dict[str, dict[str, tuple[float, float]]]) -> None:
        """Replace auto trims; anything the user has touched stays as they left it."""
        for eid, ranges in found.items():
            self.db.execute(
                "DELETE FROM trims WHERE series_key=? AND episode_id=? AND source='auto'",
                (key, eid))
            for kind, (s, e) in ranges.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO trims (series_key, episode_id, kind, start, end, source)"
                    " VALUES (?,?,?,?,?,'auto')", (key, eid, kind, s, e))

    # -- timelines (chronological recipes) --------------------------------------
    def _timeline_row(self, r: Any, with_doc: bool = False) -> dict[str, Any]:
        doc = json.loads(r["doc"])
        out = {"id": r["id"], "series_key": r["series_key"], "title": r["title"],
               "parts": len(doc["parts"]), "sources": len(doc["sources"]),
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
        self.db.execute("UPDATE timelines SET title = ?, doc = ?, updated_at = ? WHERE id = ?",
                        (clean["title"], dumps(clean),
                         _dt.datetime.now().isoformat(timespec="seconds"), tid))
        self.db.execute("DELETE FROM timeline_syncs WHERE timeline_id = ?", (tid,))
        return self.get_timeline(tid)

    def delete_timeline(self, tid: str) -> None:
        if self.db.one("SELECT id FROM timelines WHERE id = ?", (tid,)) is None:
            raise NotFoundError(f"timeline {tid} not found")
        self.db.execute("DELETE FROM timelines WHERE id = ?", (tid,))
        self.db.execute("DELETE FROM timeline_syncs WHERE timeline_id = ?", (tid,))

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
        return {**json.loads(row["report"]), "updated_at": row["updated_at"]}

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
        now = _dt.datetime.now().isoformat(timespec="seconds")
        self.db.execute(
            "INSERT OR REPLACE INTO timeline_syncs (timeline_id, report, updated_at)"
            " VALUES (?,?,?)", (tid, dumps(report), now))
        return {"sources": len(sources), "boundaries": total,
                "unmatched": len(report["unmatched_sources"]),
                "message": f"synced {total} cut points over {len(sources)} source(s)"}

    # -- timeline rendering -------------------------------------------------------
    def _source_files(self, key: str, doc: dict[str, Any]
                      ) -> dict[str, tuple[str | None, float | None]]:
        """Every source in the recipe -> (file, duration) of the user's copy, or None."""
        episodes = {e.id: e for e in self.library.episodes(key)}
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

    def timeline_dry_run(self, tid: str, mode: plan.Mode = "auto") -> dict[str, Any]:
        from .timeline import render as tl_render

        doc, key, report = self._timeline_ctx(tid)
        files = self._source_files(key, doc)
        series = self.library.get_series(key)
        corr = tl_render.corrected_map(report)
        skip = self._skip_set(report)
        show = tl_render.show_name(series.title, doc["title"])
        parts = []
        for pi, part in enumerate(doc["parts"]):
            cut, warns = tl_render.part_cut(doc, pi, files, corr, skip)
            if not cut.segments:
                parts.append({"part": pi + 1, "title": part["title"], "skipped": True,
                              "warnings": warns})
                continue
            amap, awarn = self._audio_map(list({s.file for s in cut.segments}))
            out = self._timeline_part_path(show, doc, pi, part["title"])
            rp = plan.build_plan(cut, self.cfg.ffmpeg, out, Path("<workdir>"), self.prober,
                                 mode=mode, audio_map=amap)
            parts.append({"part": pi + 1, "title": part["title"], "mode": rp.mode,
                          "reason": rp.reason, "output": rp.output,
                          "runtime": round(cut.final_runtime, 1),
                          "segments": [{**asdict(s), "duration": round(s.duration, 3)}
                                       for s in cut.segments],
                          "command": rp.command, "files": rp.files,
                          "warnings": warns + awarn + rp.warnings})
        return {"show": show, "synced": bool(report),
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
                               refresh: bool = False) -> dict[str, Any]:
        doc, _key, report = self._timeline_ctx(tid)
        if not report:
            raise FansCutError("run sync first: POST /api/timelines/{id}/sync")
        return self.worker.submit("timeline_render", f"Render {doc['title']}",
                                  {"timeline_id": tid, "mode": mode, "keep": keep,
                                   "refresh": refresh})

    def _job_timeline_render(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
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
        series = self.library.get_series(key)
        corr = tl_render.corrected_map(report)
        skip = self._skip_set(report)
        show = tl_render.show_name(series.title, doc["title"])
        movie_mode = len(doc["parts"]) == 1  # single-part edit -> Plex movie folder
        show_dir = self.cfg.output_dir / show
        show_dir.mkdir(parents=True, exist_ok=True)
        if movie_mode:
            (show_dir / f"{show}.nfo").write_text(tl_render.nfo_movie(show), encoding="utf-8")
        else:
            (show_dir / "tvshow.nfo").write_text(tl_render.nfo_show(show), encoding="utf-8")

        outputs, cached, warnings = [], 0, []
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="tl-render-", dir=self.cfg.data_dir) as tmp:
            for pi, part in enumerate(doc["parts"]):
                cut, warns = tl_render.part_cut(doc, pi, episodes_files, corr, skip)
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
                rp = plan.build_plan(cut, self.cfg.ffmpeg, out, workdir, self.prober,
                                     mode=mode, audio_map=amap)
                h = render_hash(f"timeline:{tid}:{pi + 1}", cut, rp.mode, self.cfg.ffmpeg,
                                rp.chapters)
                if not payload.get("refresh") and (hit := self.renders.hit(h)):
                    cached += 1
                    outputs.append(hit["path"])
                else:
                    ctx.progress(pi / len(doc["parts"]),
                                 f"part {pi + 1}/{len(doc['parts'])}: {part['title']}")
                    run.execute(rp, workdir, lambda p: None, ctx.cancel)
                    self.renders.drop_path(str(out), except_hash=h)
                    self.renders.add(h, key, f"{doc['title']} · part {pi + 1}", str(out),
                                     rp.mode, bool(keep), job_id=ctx.job_id)
                    outputs.append(str(out))
                if not movie_mode:
                    nfo = tl_render.nfo_episode(part["title"], pi + 1)
                    out.with_suffix(".nfo").write_text(nfo, encoding="utf-8")
        plex_note = self._plex_refresh()
        return {"show": show, "parts": len(outputs), "cached": cached, "outputs": outputs,
                "warnings": warnings, "plex": plex_note,
                "message": f"wrote {len(outputs)} part(s) to {show_dir} ({cached} cached)"}

    def _plex_refresh(self) -> str:
        """Ask Plex to rescan show sections after a timeline render. Best effort."""
        if not (self.cfg.library.plex.url and self.cfg.library.plex.token):
            return "no Plex configured"
        try:
            plex = self.library.plex()
            assert plex is not None
            want = set(self.cfg.library.plex.sections)
            refreshed = 0
            for s in plex.sections():
                if s["type"] == "show" and (not want or s["key"] in want or s["title"] in want):
                    plex.refresh_section(s["key"])
                    refreshed += 1
            return f"plex refreshed {refreshed} section(s)"
        except Exception as exc:  # noqa: BLE001 - refresh is a courtesy, never fatal
            return f"plex refresh failed: {exc}"

    # -- preview clips (sync review and the timeline editor) -----------------------
    def preview_clip(self, key: str, episode_id: str, t: float) -> Path:
        """10 s around t (5 s each side) as a tiny mp4, cached in data_dir/previews."""
        import hashlib

        episodes = {e.id: e for e in self.library.episodes(key)}
        ep = episodes.get(episode_id)
        if not ep or not ep.file or not Path(ep.file).is_file():
            raise NotFoundError(f"{episode_id} has no file in this library")
        st = Path(ep.file).stat()
        raw = f"{ep.file}|{st.st_mtime}|{st.st_size}|{t:.2f}"
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        out = self.cfg.data_dir / "previews" / f"{digest}.mp4"
        if not out.is_file():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp.mp4")
            ffmpeg.run_capture(
                [self.cfg.ffmpeg.binary, "-v", "error", "-ss", f"{max(t - 5, 0):.3f}", "-t", "10",
                 "-i", ep.file, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                 "-vf", "scale=640:-2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
                 "-movflags", "+faststart", str(tmp)])
            tmp.replace(out)
        return out

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

    # -- AI-assisted recipes ----------------------------------------------------
    def ai_recipe(self, key: str, notes: str, apply: bool = False) -> dict[str, Any]:
        """Community cut notes -> validated recipe (schema 2); optionally applied."""
        series = self.library.get_series(key)
        doc = ai_mod.generate_recipe(self.cfg.ai, series, self.library.episodes(key),
                                     notes, session=self.session)
        out: dict[str, Any] = {"recipe": doc}
        if apply:
            out["import"] = self.import_recipe(key, doc)
        return out

    def ai_outline(self, key: str, notes: str) -> dict[str, Any]:
        """Chronology notes -> scene outline (parts with placeholder segments)."""
        series = self.library.get_series(key)
        outline = ai_mod.generate_outline(self.cfg.ai, series, self.library.episodes(key),
                                          notes, session=self.session)
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

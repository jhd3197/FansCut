"""The app's brain: ties config, SQLite state, library, catalogues, labels, planner and renderer.

Routes stay thin and call into this; so do the job handlers.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

import requests

from . import recipe as recipe_mod
from .catalogues import match as catalogue_match
from .catalogues.http import CachedHttp
from .config import Config
from .db import Database, dumps
from .errors import FansCutError, NotFoundError
from .fillers import anifiller, jikan_flags
from .fillers import labels as labelling
from .fingerprint import detect
from .jobs import JobContext, JobStore, Worker
from .library.service import Library
from .models import LABELS, TRIM_KINDS, Episode
from .planner import PRESETS, CutOptions, CutPlan, PlanEpisode, plan_cut
from .render import ffmpeg, plan, run

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
        self.worker = Worker(self.jobs, {"render": self._job_render, "detect": self._job_detect})
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
        settings = self.settings(key)
        opts = _opts_from(settings, None)
        plan_eps = self._plan_episodes(episodes, labels, overrides, trims)
        from .planner import wants
        rows = []
        for e, pe in zip(episodes, plan_eps, strict=True):
            keep, why = wants(pe, opts)
            rows.append({
                "id": e.id, "season": e.season, "number": e.number, "absolute": e.absolute,
                "title": e.title, "air_date": e.air_date, "duration": e.duration,
                "has_file": bool(e.file),
                "label": labels[e.id].label, "label_source": labels[e.id].source,
                "override": overrides.get(e.id), "include": keep, "include_reason": why,
                "trims": trims.get(e.id, {}),
            })
        return {"series": asdict(series), "settings": settings, "filler_sources": sources,
                "matches": self.matches(key), "episodes": rows}

    def _plan_episodes(self, episodes: list[Episode], labels: dict[str, labelling.LabelResult],
                       overrides: dict[str, dict[str, Any]],
                       trims: dict[str, dict[str, dict[str, Any]]]) -> list[PlanEpisode]:
        out = []
        for e in episodes:
            t = {k: (v["start"], v["end"]) for k, v in trims.get(e.id, {}).items()
                 if v["start"] is not None and v["end"] is not None}
            out.append(PlanEpisode(
                id=e.id, number=e.number, season=e.season, file=e.file, duration=e.duration,
                label=labels[e.id].label, include=(overrides.get(e.id) or {}).get("include"),
                trims=t, title=e.title))
        return out

    def build_cut(self, key: str, seasons: list[int] | None = None,
                  options: dict[str, Any] | None = None) -> tuple[CutPlan, list[Episode]]:
        episodes = self._in_seasons(self.library.episodes(key), seasons)
        if not episodes:
            raise NotFoundError("no episodes in that selection")
        labels, _ = self.labels_for(key, episodes)
        opts = _opts_from(self.settings(key), options)
        plan_eps = self._plan_episodes(episodes, labels, self.overrides(key), self.trims(key))
        return plan_cut(plan_eps, opts), episodes

    # -- rendering ------------------------------------------------------------
    def render_plan(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                    mode: plan.Mode, workdir: Path) -> plan.RenderPlan:
        cut, episodes = self.build_cut(key, seasons, options)
        series = self.library.get_series(key)
        suffix = f" - S{'+'.join(f'{s:02d}' for s in seasons)}" if seasons else ""
        stem = plan.safe_name(f"{series.title}{suffix} - fan cut")
        out = plan.unique_path(self.cfg.output_dir, stem, self.cfg.ffmpeg.container)
        titles = {e.id: f"{e.id} {e.title}".strip() for e in episodes}
        return plan.build_plan(cut, self.cfg.ffmpeg, out, workdir, self.prober, titles, mode)

    def dry_run(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                mode: plan.Mode) -> dict[str, Any]:
        rp = self.render_plan(key, seasons, options, mode, Path("<workdir>"))
        cut, _ = self.build_cut(key, seasons, options)
        return {"cut": cut.to_dict(), "render": rp.to_dict()}

    def submit_render(self, key: str, seasons: list[int] | None, options: dict[str, Any] | None,
                      mode: str) -> dict[str, Any]:
        series = self.library.get_series(key)
        self.build_cut(key, seasons, options)  # fail fast on a bad request
        return self.worker.submit("render", f"Render {series.title}",
                                  {"series_key": key, "seasons": seasons, "options": options,
                                   "mode": mode})

    def _job_render(self, payload: dict[str, Any], ctx: JobContext) -> dict[str, Any]:
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="render-", dir=self.cfg.data_dir) as tmp:
            rp = self.render_plan(payload["series_key"], payload.get("seasons"),
                                  payload.get("options"), payload.get("mode", "auto"), Path(tmp))
            ctx.progress(0, f"rendering ({rp.mode}): {rp.reason}")
            out = run.execute(rp, Path(tmp), ctx.progress, ctx.cancel)
        return {"output": out, "mode": rp.mode, "message": f"wrote {out}"}

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
                                     lambda p: ctx.progress(p))
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

    # -- recipes --------------------------------------------------------------
    def export_recipe(self, key: str) -> dict[str, Any]:
        series = self.library.get_series(key)
        m, _ = self.match_for(key, WHOLE)
        # Detected and hand-edited ranges both go in; a cleared one exports as null.
        trims = {eid: {k: None if v["start"] is None else (v["start"], v["end"])
                       for k, v in t.items()} for eid, t in self.trims(key).items()}
        return recipe_mod.build(series.title, m["mal_id"] if m else None,
                                m["anilist_id"] if m else None, self.settings(key),
                                self.overrides(key), trims)

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

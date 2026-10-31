"""Library series, per-series matching, labels, overrides and trims."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..core import UNSET, WHOLE
from ..models import Series
from ..planner import PRESETS
from . import CoreDep

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/library/series")
def list_series(core: CoreDep, q: str = "", source: str = "") -> dict[str, Any]:
    series, errors = core.library.list_series()
    matched = {r["series_key"] for r in core.db.query("SELECT DISTINCT series_key FROM matches")}
    needle = q.lower().strip()
    out = []
    for s in series:
        if (source and s.source != source) or (needle and needle not in s.title.lower()):
            continue
        out.append({**asdict(s), "matched": s.key in matched})
    return {"sources": core.library.sources(), "errors": errors, "series": out}


@router.post("/library/refresh")
def refresh_library(core: CoreDep) -> dict[str, bool]:
    core.library.refresh()
    return {"ok": True}


@router.get("/series/{key}")
def get_series(core: CoreDep, key: str) -> Series:
    return core.library.get_series(key)


@router.get("/series/{key}/episodes")
def get_episodes(core: CoreDep, key: str) -> dict[str, Any]:
    return core.episode_view(key)


@router.get("/presets")
def presets() -> dict[str, Any]:
    return {"presets": list(PRESETS)}


# -- settings per series -------------------------------------------------------
class SeriesSettings(BaseModel):
    preset: str | None = None
    unknown_policy: str | None = None
    keep_intro_first_episode: bool | None = None
    pad: float | None = Field(default=None, ge=0)
    merge_gap: float | None = Field(default=None, ge=0)
    min_segment: float | None = Field(default=None, ge=0)
    transition: str | None = None


@router.get("/series/{key}/settings")
def get_settings(core: CoreDep, key: str) -> dict[str, Any]:
    core.library.get_series(key)
    return core.settings(key)


@router.put("/series/{key}/settings")
def put_settings(core: CoreDep, key: str, body: SeriesSettings) -> dict[str, Any]:
    core.library.get_series(key)
    return core.save_settings(key, body.model_dump(exclude_none=True))


# -- catalogue match -----------------------------------------------------------
class MatchBody(BaseModel):
    mal_id: int | None = None
    anilist_id: int | None = None
    title: str = ""
    season: int = WHOLE


@router.get("/series/{key}/match")
def get_match(core: CoreDep, key: str) -> dict[str, Any]:
    core.library.get_series(key)
    return core.matches_view(key)


@router.get("/series/{key}/match/candidates")
def match_candidates(core: CoreDep, key: str, q: str | None = Query(None)) -> dict[str, Any]:
    return {"candidates": core.candidates(key, q)}


@router.post("/series/{key}/match/auto")
def match_auto(core: CoreDep, key: str) -> dict[str, Any]:
    best = core.auto_match(key)
    return {"matched": best is not None, "candidate": best, **core.matches_view(key)}


@router.put("/series/{key}/match")
def put_match(core: CoreDep, key: str, body: MatchBody) -> dict[str, Any]:
    return core.set_match(key, body.mal_id, body.anilist_id, body.title, body.season)


@router.delete("/series/{key}/match")
def delete_match(core: CoreDep, key: str, season: int = WHOLE) -> dict[str, Any]:
    core.clear_match(key, season)
    return core.matches_view(key)


# -- per-episode overrides and trims -------------------------------------------
class OverrideBody(BaseModel):
    label: str | None = None
    include: bool | None = None
    # Which fields the client sent matters: null clears, absent leaves alone.


@router.put("/series/{key}/episodes/{episode_id}/override")
def put_override(core: CoreDep, key: str, episode_id: str, body: OverrideBody) -> dict[str, Any]:
    sent = body.model_fields_set
    core.set_override(key, episode_id,
                      body.label if "label" in sent else UNSET,
                      body.include if "include" in sent else UNSET)
    return core.overrides(key).get(episode_id) or {"label": None, "include": None}


@router.delete("/series/{key}/episodes/{episode_id}/override")
def delete_override(core: CoreDep, key: str, episode_id: str) -> dict[str, bool]:
    core.set_override(key, episode_id, None, None)
    return {"ok": True}


class TrimBody(BaseModel):
    start: float | None = None  # both null = "no such range here", kept against re-detection
    end: float | None = None


@router.put("/series/{key}/episodes/{episode_id}/trims/{kind}")
def put_trim(core: CoreDep, key: str, episode_id: str, kind: str, body: TrimBody) -> dict[str, Any]:
    core.set_trim(key, episode_id, kind, body.start, body.end)
    return core.trims(key).get(episode_id, {})


@router.delete("/series/{key}/episodes/{episode_id}/trims/{kind}")
def reset_trim(core: CoreDep, key: str, episode_id: str, kind: str) -> dict[str, Any]:
    """Forget the user's edit (or clear); the next detection run can fill it again."""
    core.reset_trim(key, episode_id, kind)
    return core.trims(key).get(episode_id, {})


class DetectBody(BaseModel):
    season: int


@router.post("/series/{key}/detect-trims", status_code=202)
def detect_trims(core: CoreDep, key: str, body: DetectBody) -> dict[str, Any]:
    return core.submit_detect(key, body.season)

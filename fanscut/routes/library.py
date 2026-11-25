"""Library series, per-series matching, labels, overrides and trims."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ..art import art_ref, media_type
from ..core import UNSET, WHOLE
from ..errors import NotFoundError
from ..models import Series
from ..planner import PRESETS
from ..security import rate_limit
from . import CoreDep, paginate

router = APIRouter(prefix="/api", tags=["library"])


@router.get("/library/series")
def list_series(core: CoreDep, q: str = "", source: str = "", skip: int | None = None,
                take: int | None = None) -> dict[str, Any]:
    series, errors = core.library.list_series()
    matched = {r["series_key"] for r in core.db.query("SELECT DISTINCT series_key FROM matches")}
    needle = q.lower().strip()
    out = []
    for s in series:
        if (source and s.source != source) or (needle and needle not in s.title.lower()):
            continue
        out.append({**asdict(s), "matched": s.key in matched, "art": art_ref(s)})
    page, info = paginate(out, skip, take)
    return {"sources": core.library.sources(), "errors": errors, "series": page,
            **({"page_info": info} if info else {})}


@router.get("/library/check", dependencies=[Depends(rate_limit("check", 6, 60))])
def library_check(core: CoreDep, q: str = "", deep: bool = False) -> dict[str, Any]:
    """Editions, languages, incomplete files, missing pieces, mismatches, renames."""
    return core.library_check(q=q, deep=deep)


class RenameBody(BaseModel):
    file: str
    to: str


@router.post("/library/rename")
def library_rename(core: CoreDep, body: RenameBody) -> dict[str, str]:
    """Apply one rename the library check proposed (after the user confirmed it)."""
    return core.rename_file(body.file, body.to)


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


# -- preview clips and scene search -----------------------------------------------
@router.get("/series/{key}/episodes/{episode_id}/preview",
            dependencies=[Depends(rate_limit("preview", 60, 60))])
def preview(core: CoreDep, key: str, episode_id: str, t: float = Query(ge=0)) -> FileResponse:
    """10 s around t, for reviewing a cut point in the browser."""
    return FileResponse(core.preview_clip(key, episode_id, t), media_type="video/mp4")


class SimilarBody(BaseModel):
    episode_id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)


@router.post("/series/{key}/find-similar", status_code=202)
def find_similar(core: CoreDep, key: str, body: SimilarBody) -> dict[str, Any]:
    """Find this range's audio in the rest of the series (flashbacks, reused scenes)."""
    return core.submit_find_similar(key, body.episode_id, body.start, body.end)


# -- custom segments ("my own cut") ---------------------------------------------
class SegmentsBody(BaseModel):
    segments: list[list[float]] | None = None  # kept [start, end] pairs; null/[] clears


@router.put("/series/{key}/episodes/{episode_id}/segments")
def put_segments(core: CoreDep, key: str, episode_id: str, body: SegmentsBody) -> dict[str, Any]:
    core.set_segments(key, episode_id, body.segments)
    return {"segments": core.custom_segments(key).get(episode_id) or []}


@router.delete("/series/{key}/episodes/{episode_id}/segments")
def delete_segments(core: CoreDep, key: str, episode_id: str) -> dict[str, bool]:
    core.set_segments(key, episode_id, None)
    return {"ok": True}

@router.get("/art/{kind}/{key:path}")
def artwork(core: CoreDep, kind: str, key: str, request: Request,
            w: int = Query(500, ge=50, le=2000)) -> Response:
    """A library item's poster or backdrop, from Plex, Radarr/Sonarr or files beside it.

    X-Art-Cache says HIT, STALE (served while it refreshes) or MISS (fetched now).
    The browser may keep it as long as the server cache will, and revalidates
    with If-None-Match for a cheap 304 after that."""
    art = core.art.lookup(key, kind, w)
    if art is None:
        raise NotFoundError(f"no {kind} for {key}")
    headers = {"Cache-Control": f"public, max-age={max(60, art.max_age)}",
               "ETag": art.etag, "X-Art-Cache": art.status}
    if request.headers.get("if-none-match") == art.etag:
        return Response(status_code=304, headers=headers)
    return Response(art.data, media_type=media_type(art.data), headers=headers)

"""Timeline recipes: import, inspect, update, delete. Sync and render live here too."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from . import CoreDep

router = APIRouter(prefix="/api", tags=["timelines"])


@router.get("/series/{key}/timelines")
def list_timelines(core: CoreDep, key: str) -> dict[str, Any]:
    return {"timelines": core.list_timelines(key)}


@router.post("/series/{key}/timelines", status_code=201)
def create_timeline(core: CoreDep, key: str, body: dict[str, Any]) -> dict[str, Any]:
    return core.create_timeline(key, body)


@router.get("/timelines/{tid}")
def get_timeline(core: CoreDep, tid: str) -> dict[str, Any]:
    return core.get_timeline(tid)


@router.put("/timelines/{tid}")
def update_timeline(core: CoreDep, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    return core.update_timeline(tid, body)


@router.delete("/timelines/{tid}")
def delete_timeline(core: CoreDep, tid: str) -> dict[str, bool]:
    core.delete_timeline(tid)
    return {"ok": True}


@router.get("/timelines/{tid}/export")
def export_timeline(core: CoreDep, tid: str,
                    anchors: str = Query("both", pattern="^(both|audio|video|none)$")
                    ) -> dict[str, Any]:
    """The recipe with cut-point anchors computed from the author's files."""
    kinds = {"both": ("a", "v"), "audio": ("a",), "video": ("v",), "none": ()}[anchors]
    if not kinds:
        return core.get_timeline(tid)["doc"]
    return core.export_timeline(tid, kinds)


@router.post("/timelines/{tid}/sync", status_code=202)
def sync_timeline(core: CoreDep, tid: str) -> dict[str, Any]:
    return core.submit_timeline_sync(tid)


@router.get("/timelines/{tid}/sync")
def sync_report(core: CoreDep, tid: str) -> dict[str, Any]:
    report = core.timeline_sync_report(tid)
    if report is None:
        return {"synced": False}
    return {"synced": True, **report}


class TimelineRenderBody(BaseModel):
    dry_run: bool = False
    mode: Literal["auto", "copy", "encode"] = "auto"
    keep: bool | None = None
    refresh: bool = False
    variant: str | None = Field(default=None, max_length=40)
    toggles: dict[str, bool] | None = None
    collection: bool = True  # add the result to Plex's "FansCut" collection


@router.post("/timelines/{tid}/render")
def render_timeline(core: CoreDep, tid: str, body: TimelineRenderBody | None = None
                    ) -> dict[str, Any]:
    """dry_run=true returns the per-part plans; otherwise queues a render job."""
    body = body or TimelineRenderBody()
    if body.dry_run:
        return core.timeline_dry_run(tid, body.mode, body.variant, body.toggles)
    return core.submit_timeline_render(tid, body.mode, body.keep, body.refresh,
                                       variant=body.variant, toggles=body.toggles,
                                       collection=body.collection)


class CutPointBody(BaseModel):
    part: int = Field(ge=1)
    segment: int = Field(ge=1)
    side: Literal["start", "end"]
    t: float | None = Field(default=None, ge=0, le=86_400)  # None = what sync found


@router.put("/timelines/{tid}/cut-points")
def set_cut_point(core: CoreDep, tid: str, body: CutPointBody) -> dict[str, Any]:
    """Nudge one cut point in the user's copy; the report comes back with it applied."""
    return core.set_cut_point(tid, body.part, body.segment, body.side, body.t)


@router.get("/timelines/{tid}/estimate")
def timeline_estimate(core: CoreDep, tid: str, variant: str | None = None) -> dict[str, Any]:
    """About how long a build takes on this machine, and why."""
    return core.timeline_estimate(tid, variant or None)


class OptionsBody(BaseModel):
    auto_rebuild: bool


@router.put("/timelines/{tid}/options")
def timeline_options(core: CoreDep, tid: str, body: OptionsBody) -> dict[str, Any]:
    """auto_rebuild: when an update changes the recipe, sync and rebuild (unchanged parts
    are reused from the cache)."""
    return core.set_timeline_options(tid, body.auto_rebuild)


@router.get("/timelines/{tid}/variants")
def timeline_variants(core: CoreDep, tid: str) -> dict[str, Any]:
    """Toggles (disabled ones name the film they need) and each variant's runtime."""
    return core.timeline_variants(tid)

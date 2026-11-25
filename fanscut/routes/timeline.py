"""Timeline recipes: import, inspect, update, delete. Sync and render live here too."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

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

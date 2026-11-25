"""Timeline recipes: import, inspect, update, delete. Sync and render live here too."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

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

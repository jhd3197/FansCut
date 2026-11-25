"""Rendered outputs: list, keep/evict flag, delete, sweep."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from . import CoreDep

router = APIRouter(prefix="/api", tags=["renders"])


@router.get("/renders")
def list_renders(core: CoreDep, series: str | None = None) -> dict[str, Any]:
    return {"renders": core.list_renders(series)}


class KeepBody(BaseModel):
    keep: bool


@router.put("/renders/{h}")
def set_keep(core: CoreDep, h: str, body: KeepBody) -> dict[str, Any]:
    return core.set_render_keep(h, body.keep)


@router.delete("/renders/{h}")
def delete_render(core: CoreDep, h: str) -> dict[str, bool]:
    core.delete_render(h)
    return {"ok": True}


@router.post("/renders/sweep")
def sweep(core: CoreDep) -> dict[str, Any]:
    return core.sweep_renders()

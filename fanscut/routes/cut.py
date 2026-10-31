"""Cut plan, render (dry run or job), and recipes."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import CoreDep

router = APIRouter(prefix="/api", tags=["cut"])


class CutBody(BaseModel):
    seasons: list[int] | None = None
    options: dict[str, Any] | None = Field(default=None, description="cut settings for this run")


class RenderBody(CutBody):
    dry_run: bool = False
    mode: Literal["auto", "copy", "encode"] = "auto"


@router.post("/series/{key}/cut")
def make_cut(core: CoreDep, key: str, body: CutBody | None = None) -> dict[str, Any]:
    body = body or CutBody()
    cut, _ = core.build_cut(key, body.seasons, body.options)
    return cut.to_dict()


@router.post("/series/{key}/render")
def render(core: CoreDep, key: str, body: RenderBody | None = None) -> dict[str, Any]:
    """dry_run=true returns the cut and ffmpeg plan; otherwise queues a render job."""
    body = body or RenderBody()
    if body.dry_run:
        return core.dry_run(key, body.seasons, body.options, body.mode)
    return core.submit_render(key, body.seasons, body.options, body.mode)


@router.get("/series/{key}/recipe")
def export_recipe(core: CoreDep, key: str) -> dict[str, Any]:
    return core.export_recipe(key)


@router.post("/series/{key}/recipe")
def import_recipe(core: CoreDep, key: str, body: dict[str, Any]) -> dict[str, Any]:
    return core.import_recipe(key, body)

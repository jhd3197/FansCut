"""Read models for the UI: movies, collections, details, my cuts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import browse
from ..security import rate_limit
from . import CoreDep, paginate

router = APIRouter(prefix="/api", tags=["browse"])


@router.get("/library/movies")
def list_movies(core: CoreDep, skip: int | None = None,
                take: int | None = None) -> dict[str, Any]:
    out = browse.movies(core)
    if take is not None and isinstance(out.get("movies"), list):
        out["movies"], info = paginate(out["movies"], skip, take)
        out["page_info"] = info
    return out


@router.get("/library/movies/{key:path}")
def get_movie(core: CoreDep, key: str) -> dict[str, Any]:
    """Probe details, health findings, cuts using it, collection siblings."""
    return browse.movie(core, key)


@router.get("/library/collections")
def list_collections(core: CoreDep) -> dict[str, Any]:
    return browse.collections(core)


@router.get("/library/collections/{name}")
def get_collection(core: CoreDep, name: str) -> dict[str, Any]:
    return browse.collection(core, name)


@router.get("/library/check/last")
def last_check(core: CoreDep) -> dict[str, Any]:
    """The last whole-library check, without running a new one."""
    return core.last_check() or {"summary": None, "findings": [], "renames": [], "files": []}


@router.get("/series/{key}/episodes/{episode_id}/info")
def episode_info(core: CoreDep, key: str, episode_id: str) -> dict[str, Any]:
    return browse.episode(core, key, episode_id)


class InstallBody(BaseModel):
    url: str = Field(max_length=300)
    ref: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9._/-]+$")


@router.get("/catalogue")
def catalogue(core: CoreDep) -> dict[str, Any]:
    """The community index, each cut rated ready / partly / missing for this library."""
    return core.catalogue()


@router.post("/cuts/install", dependencies=[Depends(rate_limit("install", 10, 60))])
def install_cut(core: CoreDep, body: InstallBody) -> dict[str, Any]:
    """Install (or update) a cut from its GitHub repo; records the commit SHA."""
    return core.install_cut(body.url, body.ref)


@router.get("/cuts/updates")
def cut_updates(core: CoreDep) -> dict[str, Any]:
    return core.cut_updates()


@router.post("/cuts/{tid}/update")
def update_cut(core: CoreDep, tid: str) -> dict[str, Any]:
    return core.update_cut(tid)


class TemplateBody(BaseModel):
    title: str = Field(default="", max_length=200)
    author: str = Field(default="", max_length=100)
    movies: list[str] = Field(default_factory=list, max_length=50)
    series_key: str = Field(default="", max_length=300)
    push: dict[str, Any] | None = None  # {"name": "took", "private": true}


@router.get("/templates")
def list_templates() -> dict[str, Any]:
    from ..templates import TEMPLATES

    return {"templates": TEMPLATES}


@router.post("/templates/{tid}/preview")
def preview_template(core: CoreDep, tid: str, body: TemplateBody) -> dict[str, Any]:
    """README.md, fanscut.json and outline.md, without saving anything."""
    return core.template_preview(tid, body.model_dump())


@router.post("/templates/{tid}/create")
def create_from_template(core: CoreDep, tid: str, body: TemplateBody) -> dict[str, Any]:
    """Save the files, add the timeline, and push a private repo when asked."""
    return core.template_create(tid, body.model_dump())


@router.get("/cuts")
def my_cuts(core: CoreDep) -> dict[str, Any]:
    """Every timeline and every rendered file."""
    return browse.all_cuts(core)

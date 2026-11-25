"""First-run suggestions: list and install."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import CoreDep

router = APIRouter(prefix="/api", tags=["suggestions"])


@router.get("/suggestions")
def list_suggestions(core: CoreDep) -> dict[str, Any]:
    return core.suggestions()


@router.post("/suggestions/{sid}/install")
def install(core: CoreDep, sid: str) -> dict[str, Any]:
    return core.install_suggestion(sid)

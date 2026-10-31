from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import CoreDep

router = APIRouter(prefix="/api/fillers", tags=["fillers"])


@router.get("")
def status(core: CoreDep) -> dict[str, Any]:
    return core.fillers_status()


@router.post("/refresh")
def refresh(core: CoreDep) -> dict[str, Any]:
    """Download the AniFiller snapshot; a failure is reported in the body, not raised."""
    return core.refresh_fillers()

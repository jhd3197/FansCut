"""Inbound webhooks. No auth, like the rest of the app: keep it on a trusted network."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import CoreDep

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/sonarr")
def sonarr_webhook(core: CoreDep, body: dict[str, Any]) -> dict[str, Any]:
    """Point a Sonarr "Webhook" connection at this URL; see automation.* settings."""
    return core.handle_sonarr_webhook(body)

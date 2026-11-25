"""Inbound webhooks. They always need the API key: ?apikey=<key> on the URL, an
X-Api-Key header, or the key as the Basic-auth password (Sonarr's Username/Password)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..security import require_key
from . import CoreDep

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/sonarr")
def sonarr_webhook(core: CoreDep, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Point a Sonarr "Webhook" connection at this URL; see automation.* settings."""
    require_key(request, core.cfg.server)
    return core.handle_sonarr_webhook(body)

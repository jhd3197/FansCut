"""Sign in with Plex (plex.tv PIN flow): start, poll, then save the token."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import config, plextv
from ..errors import FansCutError
from ..security import rate_limit
from . import CoreDep

router = APIRouter(prefix="/api/plex", tags=["settings"])


@router.post("/pin", dependencies=[Depends(rate_limit("plexpin", 10, 60))])
def start_pin(core: CoreDep) -> dict[str, Any]:
    """A PIN to approve at the returned auth_url (open it in a new tab)."""
    return plextv.start(plextv.client_id(core.cfg.data_dir), core.session)


class PinCheck(BaseModel):
    url: str = ""  # the server URL to save with the token; "" keeps the current one


@router.post("/pin/{pin_id}", dependencies=[Depends(rate_limit("plexpoll", 60, 60))])
def check_pin(core: CoreDep, pin_id: int, body: PinCheck, request: Request) -> dict[str, Any]:
    """Poll a PIN. Once approved: save the token (and URL), and list the account's servers."""
    cid = plextv.client_id(core.cfg.data_dir)
    token = plextv.check(cid, pin_id, core.session)
    if not token:
        return {"done": False}
    if "library.plex.token" in config.env_locked():
        raise FansCutError("the Plex token is set by FANSCUT_PLEX_TOKEN; change it there")
    found = plextv.servers(cid, token, core.session)
    url = body.url or core.cfg.library.plex.url or next(
        (u for s in found if s["owned"] for u in s["urls"]), "")
    new = core.cfg.model_copy(deep=True)
    new.library.plex.token = token
    if url:
        new.library.plex.url = url
    path: Path = request.app.state.config_path
    try:
        config.save(new, path)
    except OSError as exc:
        raise FansCutError(f"could not write {path}: {exc.strerror}") from exc
    core.apply_config(new)
    return {"done": True, "servers": found, "url": url}

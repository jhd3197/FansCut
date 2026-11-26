"""The API-key handshake for server.auth = "api_key": log in once, get a cookie."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..security import COOKIE, presented_key, rate_limit, role_of, session_token
from . import CoreDep

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Login(BaseModel):
    key: str


@router.get("/status")
def auth_status(core: CoreDep, request: Request) -> dict[str, Any]:
    """Whether a key is needed, and the role the current request has."""
    server = core.cfg.server
    role = role_of(presented_key(request), server)
    if server.auth == "none":
        role = role or "admin"
    return {"auth": server.auth, "role": role}


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 60))])
def login(core: CoreDep, body: Login, request: Request, response: Response) -> dict[str, Any]:
    role = role_of(body.key.strip(), core.cfg.server)
    if role is None:
        raise HTTPException(401, "wrong key")
    # the cookie holds a token derived from the configured key, never the key itself
    stored = core.cfg.server.api_key if role == "admin" else core.cfg.server.viewer_key
    response.set_cookie(COOKIE, session_token(stored), httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", max_age=30 * 86400)
    return {"role": role}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/key")
def api_key(core: CoreDep, request: Request) -> dict[str, str]:
    """The admin API key, for pasting into Sonarr's webhook URL. Admins only."""
    server = core.cfg.server
    role = role_of(presented_key(request), server)
    if server.auth == "api_key" and role != "admin":
        raise HTTPException(401, "API key required")
    return {"api_key": server.api_key}


@router.post("/key/regenerate", dependencies=[Depends(rate_limit("login", 10, 60))])
def regenerate_key(core: CoreDep, request: Request) -> dict[str, str]:
    """A new admin API key; the old one (and any webhook using it) stops working."""
    from pathlib import Path

    from .. import config
    from ..errors import FansCutError
    from ..security import new_key

    server = core.cfg.server
    if server.auth == "api_key" and role_of(presented_key(request), server) != "admin":
        raise HTTPException(401, "API key required")
    new = core.cfg.model_copy(deep=True)
    new.server.api_key = new_key()
    path: Path = request.app.state.config_path
    try:
        config.save(new, path)
    except OSError as exc:
        raise FansCutError(f"could not write {path}: {exc.strerror}") from exc
    core.apply_config(new)
    return {"api_key": new.server.api_key}

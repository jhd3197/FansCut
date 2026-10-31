"""Health, doctor, settings and connection tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import __version__, config, doctor
from ..clients.plex import PlexClient
from ..clients.sonarr import SonarrClient
from ..errors import ClientError, FansCutError
from . import CoreDep

router = APIRouter()


@router.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/api/doctor", tags=["system"])
def get_doctor(core: CoreDep) -> dict[str, Any]:
    return doctor.run(core.cfg)


@router.get("/api/settings", tags=["settings"])
def get_settings(core: CoreDep) -> dict[str, Any]:
    return config.masked(core.cfg) | {"env_locked": config.env_locked()}


@router.put("/api/settings", tags=["settings"])
def put_settings(core: CoreDep, body: dict[str, Any], request: Request) -> dict[str, Any]:
    new = config.merge_update(core.cfg, body)
    path: Path = request.app.state.config_path
    try:
        config.save(new, path)
    except OSError as exc:
        raise FansCutError(f"could not write {path}: {exc.strerror}") from exc
    core.apply_config(new)
    return config.masked(new) | {"env_locked": config.env_locked()}


class TestBody(BaseModel):
    url: str | None = None
    api_key: str | None = None
    token: str | None = None
    path: str | None = None


@router.post("/api/settings/test/{service}", tags=["settings"])
def test_connection(core: CoreDep, service: str, body: TestBody | None = None) -> dict[str, Any]:
    """Try a service with the posted values (falling back to saved ones); never raises."""
    body = body or TestBody()
    lib = core.cfg.library
    use = lambda posted, saved: saved if posted in (None, config.MASK) else posted  # noqa: E731
    try:
        if service == "sonarr":
            st = SonarrClient(use(body.url, lib.sonarr.url),
                              use(body.api_key, lib.sonarr.api_key)).system_status()
            return {"ok": True, "message": f"Sonarr {st.get('version', '')}".strip()}
        if service == "plex":
            ident = PlexClient(use(body.url, lib.plex.url),
                               use(body.token, lib.plex.token)).identity()
            return {"ok": True, "message": f"Plex {ident.get('version', '')}".strip()}
        if service == "folder":
            p = Path(use(body.path, lib.folder.path))
            return {"ok": p.is_dir(), "message": "folder found" if p.is_dir() else "not a folder"}
    except (ClientError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    raise FansCutError(f"unknown service {service!r}")

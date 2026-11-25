"""Health, doctor, settings and connection tests."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from .. import __version__, config, doctor
from ..clients.plex import PlexClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..errors import ClientError, FansCutError
from ..security import rate_limit
from . import CoreDep

TEST_TIMEOUT = 8.0

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


@router.post("/api/settings/detect", tags=["settings"])
def detect_local_apps(core: CoreDep, request: Request) -> dict[str, Any]:
    """Fill empty Plex/Radarr/Sonarr settings from the apps on this machine and save."""
    from ..library import autodetect

    found = autodetect.detect()
    new = core.cfg.model_copy(deep=True)
    filled = autodetect.fill(new, config.env_locked(), found)
    if filled:
        path: Path = request.app.state.config_path
        try:
            config.save(new, path)
        except OSError as exc:
            raise FansCutError(f"could not write {path}: {exc.strerror}") from exc
        core.apply_config(new)
    return {"filled": filled,
            "found": [{"service": f.service, "url": f.url, "source": f.source} for f in found],
            "settings": config.masked(core.cfg) | {"env_locked": config.env_locked()}}


@router.put("/api/settings", tags=["settings"])
def put_settings(core: CoreDep, body: dict[str, Any], request: Request) -> dict[str, Any]:
    new = config.merge_update(core.cfg, body, from_http=True)
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


def _secret_for(posted_url: str | None, saved_url: str, posted: str | None, saved: str) -> str:
    """The secret to test with. A saved secret is only ever sent to the saved URL: a
    new URL needs its key typed in again, so a test cannot leak the stored one."""
    if posted not in (None, "", config.MASK):
        return posted or ""
    if posted_url not in (None, "") and posted_url.rstrip("/") != saved_url.rstrip("/"):
        raise ValueError("enter the key again to test a different URL")
    return saved


@router.post("/api/settings/test/{service}", tags=["settings"],
             dependencies=[Depends(rate_limit("test", 20, 60))])
def test_connection(core: CoreDep, service: str, body: TestBody | None = None) -> dict[str, Any]:
    """Try a service with the posted values (falling back to saved ones); never raises."""
    body = body or TestBody()
    lib = core.cfg.library
    url = lambda posted, saved: saved if posted in (None, "") else posted  # noqa: E731
    try:
        if service == "sonarr":
            key = _secret_for(body.url, lib.sonarr.url, body.api_key, lib.sonarr.api_key)
            client = SonarrClient(url(body.url, lib.sonarr.url), key, timeout=TEST_TIMEOUT)
            st = client.system_status()
            return {"ok": True, "message": f"Sonarr {st.get('version', '')}".strip(),
                    **_arr_extras(client)}
        if service == "radarr":
            key = _secret_for(body.url, lib.radarr.url, body.api_key, lib.radarr.api_key)
            radarr = RadarrClient(url(body.url, lib.radarr.url), key, timeout=TEST_TIMEOUT)
            st = radarr.system_status()
            return {"ok": True, "message": f"Radarr {st.get('version', '')}".strip(),
                    **_arr_extras(radarr)}
        if service == "plex":
            token = _secret_for(body.url, lib.plex.url, body.token, lib.plex.token)
            ident = PlexClient(url(body.url, lib.plex.url), token,
                               timeout=TEST_TIMEOUT).identity()
            return {"ok": True, "message": f"Plex {ident.get('version', '')}".strip()}
        if service == "folder":
            raw = url(body.path, lib.folder.path)
            p = Path(raw)
            if not p.is_absolute():
                return {"ok": False, "message": "use an absolute path"}
            return {"ok": p.is_dir(), "message": "folder found" if p.is_dir() else "not a folder"}
    except (ClientError, ValueError) as exc:
        return {"ok": False, "message": str(exc)}
    raise FansCutError(f"unknown service {service!r}")


def _arr_extras(client: Any) -> dict[str, Any]:
    """Tags and quality profiles, so the settings page can offer them (best effort)."""
    out: dict[str, Any] = {}
    for name, fn in (("tags", "tags"), ("profiles", "quality_profiles")):
        with contextlib.suppress(ClientError, AttributeError):
            out[name] = getattr(client, fn)()
    return out

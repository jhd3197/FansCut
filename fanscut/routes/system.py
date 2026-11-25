"""Health, doctor, settings and connection tests."""

from __future__ import annotations

import contextlib
import functools
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import __version__, config, doctor, logs
from ..clients.plex import PlexClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..errors import ClientError, FansCutError
from ..security import rate_limit
from . import CoreDep

TEST_TIMEOUT = 8.0

router = APIRouter()


@router.get("/healthz", tags=["system"])
def healthz(core: CoreDep, request: Request) -> JSONResponse:
    """200 when the database answers and the worker threads are up; 503 otherwise."""
    problems = []
    try:
        core.db.one("SELECT 1")
    except Exception as exc:  # noqa: BLE001 - any failure is "unhealthy"
        problems.append(f"database: {exc.__class__.__name__}")
    if request.app.state.worker_expected and not core.worker.alive():
        problems.append("worker stopped")
    body = {"status": "ok" if not problems else "unhealthy", "version": __version__,
            "commit": commit(), "problems": problems}
    return JSONResponse(body, status_code=200 if not problems else 503)


@functools.cache
def commit() -> str:
    """The build's git commit: FANSCUT_COMMIT (set in the Docker image), else the repo's HEAD."""
    env = os.environ.get("FANSCUT_COMMIT", "").strip()
    if env:
        return env[:12]
    repo = Path(__file__).resolve().parents[2]
    if not (repo / ".git").exists():
        return ""
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                             capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def restart_needed(core: Any, request: Request) -> list[str]:
    from ..server import restart_snapshot

    now = restart_snapshot(core.cfg)
    return [k for k, v in request.app.state.boot.items() if now.get(k) != v]


@router.get("/api/status", tags=["system"])
def status(core: CoreDep, request: Request) -> dict[str, Any]:
    """Version and build, uptime, queue depth, and settings waiting for a restart.

    The UI polls this to offer a reload when the server was updated."""
    pending = restart_needed(core, request)
    return {"version": __version__, "commit": commit(),
            "started_at": round(request.app.state.started),
            "uptime_seconds": round(time.time() - request.app.state.started),
            "queue": core.jobs.depth(), "worker_alive": core.worker.alive(),
            "restart_required": bool(pending), "restart_fields": pending,
            "auth": core.cfg.server.auth}


@router.get("/api/logs", tags=["system"])
def get_logs(core: CoreDep, level: str = "INFO", search: str = "",
             skip: int = Query(0, ge=0), take: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """The server log, newest first: at `level` or above, containing `search`."""
    return logs.read(core.cfg.data_dir, level, search, skip, take)


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
    pending = restart_needed(core, request)
    return config.masked(new) | {"env_locked": config.env_locked(),
                                 "restart_required": bool(pending), "restart_fields": pending}


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


# -- caches -----------------------------------------------------------------------
@router.get("/api/system/cache", tags=["system"])
def cache_stats(core: CoreDep) -> dict[str, Any]:
    """Sizes and hit counts of the art cache, the library listing cache and the
    catalogue HTTP cache."""
    row = core.db.one("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(body)), 0) AS b FROM http_cache")
    return {"art": core.art.stats(), "library": core.library.cache.summary(),
            "http": {"rows": row["n"] if row else 0, "bytes": row["b"] if row else 0}}


@router.post("/api/system/cache/{name}/clear", tags=["system"])
def cache_clear(core: CoreDep, name: str) -> dict[str, Any]:
    if name == "art":
        return {"removed": core.art.clear()}
    if name == "library":
        core.library.refresh()
        return {"removed": 0}
    if name == "http":
        n = core.db.one("SELECT COUNT(*) AS n FROM http_cache")
        core.db.execute("DELETE FROM http_cache")
        return {"removed": n["n"] if n else 0}
    raise FansCutError(f"unknown cache {name!r}")

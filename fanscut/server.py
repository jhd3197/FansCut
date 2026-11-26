"""FastAPI app: REST under /api, /healthz, docs at /docs, and ui/dist if it has been built."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__, config, logs, security
from .config import Config
from .core import Core
from .db import Database
from .errors import ClientError, FansCutError, NotFoundError
from .routes import (
    auth,
    browse,
    cut,
    fillers,
    jobs,
    library,
    plex,
    renders,
    scenes,
    suggestions,
    system,
    timeline,
    webhooks,
)

log = logging.getLogger("fanscut.server")

def ui_dir() -> Path:
    """The built UI: FANSCUT_UI_DIR, else ui/dist next to the package."""
    return Path(
        os.environ.get("FANSCUT_UI_DIR") or Path(__file__).resolve().parent.parent / "ui" / "dist"
    )


def _autodetect(cfg: Config, path: Path) -> None:
    """Fill empty Plex/Radarr/Sonarr settings from the apps on this machine and
    save them, so a local install needs no keys typed in. FANSCUT_AUTODETECT=0
    turns it off."""
    if os.environ.get("FANSCUT_AUTODETECT", "1") == "0":
        return
    from .library import autodetect

    filled = autodetect.fill(cfg, config.env_locked())
    if not filled:
        return
    log.info("filled %s from the apps on this machine", ", ".join(filled))
    try:
        config.save(cfg, path)
    except OSError as exc:
        log.warning("could not save detected settings to %s: %s", path, exc.strerror)


# Settings read once at start: changing them needs a restart to take effect.
RESTART_FIELDS = ("server.host", "server.port", "server.trusted_proxies", "paths.data_dir")


def restart_snapshot(cfg: Config) -> dict[str, object]:
    d = cfg.model_dump()
    out: dict[str, object] = {}
    for path in RESTART_FIELDS:
        v: object = d
        for k in path.split("."):
            v = v[k] if isinstance(v, dict) else None
        out[path] = v
    return out


def ensure_api_key(cfg: Config, path: Path) -> None:
    """Generate the API key on first start and save it, so webhooks have one to check."""
    if cfg.server.api_key:
        return
    cfg.server.api_key = security.new_key()
    try:
        config.save(cfg, path)
    except OSError as exc:
        log.warning("could not save the new API key to %s: %s", path, exc.strerror)


class ApiVersionMiddleware:
    """/api/v1/... is the versioned name of /api/...; both serve the same routes.

    A route on its way out can be marked with deprecate() below, which adds the
    RFC 8594 Deprecation and Sunset headers to its responses."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/v1/"):
            scope = dict(scope)
            scope["path"] = "/api/" + scope["path"][len("/api/v1/"):]
            scope["raw_path"] = scope["path"].encode()
        await self.app(scope, receive, send)


def _mount_ui(app: FastAPI, ui: Path) -> None:
    """Serve the built UI; unknown non-API paths get index.html so client-side routes reload."""
    root = ui.resolve()
    base = str(root)

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in ("api", "docs", "redoc", "openapi.json", "healthz"):
            raise HTTPException(404, "Not Found")
        # normpath, then a prefix check on that same string before any file access: the
        # form path scanners recognise as a guard against ../ escapes
        full = os.path.normpath(os.path.join(base, path))
        if path and full.startswith(base + os.sep) and os.path.isfile(full):
            if path == "sw.js":  # browsers must always see the current worker
                return FileResponse(full, headers={"Cache-Control": "no-cache",
                                                   "Service-Worker-Allowed": "/"})
            # Vite's assets/ names carry a content hash: cache them for good
            cache = ("public, max-age=31536000, immutable" if path.startswith("assets/")
                     else "no-cache")
            return FileResponse(full, headers={"Cache-Control": cache})
        # index.html names the current bundle; never let a browser keep a stale one
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(cfg: Config | None = None, config_path: Path | str | None = None,
               start_worker: bool = True) -> FastAPI:
    path = Path(config_path) if config_path else config.default_path()
    if cfg is None:  # a real start (tests pass their own config and skip this)
        cfg = config.load(path)
        logs.setup(cfg.data_dir)
        _autodetect(cfg, path)
        ensure_api_key(cfg, path)
    elif not cfg.server.api_key:
        cfg.server.api_key = security.new_key()
    core = Core(cfg, Database(cfg.db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logs.attach_uvicorn()
        log.info("FansCut %s started (data in %s)", __version__, core.cfg.data_dir)
        if start_worker:
            core.worker.start()
            core.scheduler.start()
        core.sweep_renders()
        yield
        core.scheduler.stop()
        core.worker.stop()
        core.db.close()

    app = FastAPI(title="FansCut", version=__version__, lifespan=lifespan,
                  description="Self-hosted fan cuts: skip filler, trim recaps and openings.")
    app.state.core = core
    app.state.config_path = path
    app.state.started = time.time()
    app.state.worker_expected = start_worker
    # settings that only take effect on restart, as they were at boot
    app.state.boot = restart_snapshot(cfg)
    app.add_middleware(security.GuardMiddleware, get_cfg=lambda: core.cfg)
    app.add_middleware(ApiVersionMiddleware)

    @app.exception_handler(NotFoundError)
    async def _not_found(_r: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "not_found"}, status_code=404)

    @app.exception_handler(ClientError)
    async def _upstream(_r: Request, exc: ClientError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "upstream"}, status_code=502)

    @app.exception_handler(FansCutError)
    async def _bad(_r: Request, exc: FansCutError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "bad_request"}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value(_r: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "bad_request"}, status_code=400)

    # One error shape everywhere: {"detail": str, "code": str, "errors"?: [...]}.
    @app.exception_handler(RequestValidationError)
    async def _invalid(_r: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", "")}
                  for e in exc.errors()]
        detail = "; ".join(f"{'.'.join(e['loc'][1:]) or 'body'}: {e['msg']}" for e in errors)
        return JSONResponse({"detail": detail or "invalid request", "code": "validation",
                             "errors": errors}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": str(exc.detail), "code": f"http_{exc.status_code}"},
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _crash(r: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", r.method, r.url.path)
        return JSONResponse({"detail": f"internal error: {exc.__class__.__name__}; "
                                       "see the log for details", "code": "internal"},
                            status_code=500)

    for r in (auth.router, system.router, library.router, cut.router, jobs.router, fillers.router,
              renders.router, webhooks.router, timeline.router, suggestions.router,
              browse.router, plex.router, scenes.router):
        app.include_router(r)

    ui = ui_dir()
    if (ui / "index.html").is_file():
        _mount_ui(app, ui)
    return app

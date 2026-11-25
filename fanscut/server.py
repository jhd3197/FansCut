"""FastAPI app: REST under /api, /healthz, docs at /docs, and ui/dist if it has been built."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, config, security
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
    renders,
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


def _ensure_api_key(cfg: Config, path: Path) -> None:
    """Generate the API key on first start and save it, so webhooks have one to check."""
    if cfg.server.api_key:
        return
    cfg.server.api_key = security.new_key()
    try:
        config.save(cfg, path)
    except OSError as exc:
        log.warning("could not save the new API key to %s: %s", path, exc.strerror)


def _mount_ui(app: FastAPI, ui: Path) -> None:
    """Serve the built UI; unknown non-API paths get index.html so client-side routes reload."""
    root = ui.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in ("api", "docs", "redoc", "openapi.json", "healthz"):
            raise HTTPException(404, "Not Found")
        f = (root / path).resolve()
        if path and f.is_file() and f.is_relative_to(root):
            # Vite's assets/ names carry a content hash: cache them for good
            immutable = path.startswith("assets/")
            return FileResponse(f, headers={"Cache-Control": "public, max-age=31536000, immutable"
                                            if immutable else "no-cache"})
        # index.html names the current bundle; never let a browser keep a stale one
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(cfg: Config | None = None, config_path: Path | str | None = None,
               start_worker: bool = True) -> FastAPI:
    path = Path(config_path) if config_path else config.default_path()
    if cfg is None:  # a real start (tests pass their own config and skip this)
        cfg = config.load(path)
        _autodetect(cfg, path)
        _ensure_api_key(cfg, path)
    elif not cfg.server.api_key:
        cfg.server.api_key = security.new_key()
    core = Core(cfg, Database(cfg.db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            core.worker.start()
        core.sweep_renders()
        yield
        core.worker.stop()
        core.db.close()

    app = FastAPI(title="FansCut", version=__version__, lifespan=lifespan,
                  description="Self-hosted fan cuts: skip filler, trim recaps and openings.")
    app.state.core = core
    app.state.config_path = path
    app.add_middleware(security.GuardMiddleware, get_cfg=lambda: core.cfg)

    @app.exception_handler(NotFoundError)
    async def _not_found(_r: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ClientError)
    async def _upstream(_r: Request, exc: ClientError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(FansCutError)
    async def _bad(_r: Request, exc: FansCutError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def _value(_r: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    for r in (auth.router, system.router, library.router, cut.router, jobs.router, fillers.router,
              renders.router, webhooks.router, timeline.router, suggestions.router,
              browse.router):
        app.include_router(r)

    ui = ui_dir()
    if (ui / "index.html").is_file():
        _mount_ui(app, ui)
    return app

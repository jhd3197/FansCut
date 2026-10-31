"""FastAPI app: REST under /api, /healthz, docs at /docs, and ui/dist if it has been built."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, config
from .config import Config
from .core import Core
from .db import Database
from .errors import ClientError, FansCutError, NotFoundError
from .routes import cut, fillers, jobs, library, system

log = logging.getLogger("fanscut.server")

def ui_dir() -> Path:
    """The built UI: FANSCUT_UI_DIR, else ui/dist next to the package."""
    return Path(
        os.environ.get("FANSCUT_UI_DIR") or Path(__file__).resolve().parent.parent / "ui" / "dist"
    )


def _mount_ui(app: FastAPI, ui: Path) -> None:
    """Serve the built UI; unknown non-API paths get index.html so client-side routes reload."""
    root = ui.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.split("/", 1)[0] in ("api", "docs", "redoc", "openapi.json", "healthz"):
            raise HTTPException(404, "Not Found")
        f = (root / path).resolve()
        if path and f.is_file() and f.is_relative_to(root):
            return FileResponse(f)
        return FileResponse(root / "index.html")


def create_app(cfg: Config | None = None, config_path: Path | str | None = None,
               start_worker: bool = True) -> FastAPI:
    path = Path(config_path) if config_path else config.default_path()
    cfg = cfg or config.load(path)
    core = Core(cfg, Database(cfg.db_path))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if start_worker:
            core.worker.start()
        yield
        core.worker.stop()
        core.db.close()

    app = FastAPI(title="FansCut", version=__version__, lifespan=lifespan,
                  description="Self-hosted fan cuts: skip filler, trim recaps and openings.")
    app.state.core = core
    app.state.config_path = path

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

    for r in (system.router, library.router, cut.router, jobs.router, fillers.router):
        app.include_router(r)

    ui = ui_dir()
    if (ui / "index.html").is_file():
        _mount_ui(app, ui)
    return app

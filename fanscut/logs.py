"""Logging: readable lines on stderr, JSON lines in <data_dir>/logs/fanscut.log.

The file rotates daily and keeps LOG_DAYS days, so a bug report can attach it.
FANSCUT_LOG_LEVEL (DEBUG, INFO, WARNING, ERROR) sets the level; INFO by default.
read() serves the file to the log viewer: newest first, filtered and paged.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

LOG_DAYS = 7
FILE = "fanscut.log"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_handler: logging.Handler | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
                               "level": record.levelname, "logger": record.name,
                               "message": record.getMessage()}
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def level() -> int:
    name = os.environ.get("FANSCUT_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, name, logging.INFO) if name in LEVELS else logging.INFO


def setup(data_dir: Path | None) -> None:
    """Console logging always; the rotating file once data_dir is known. Idempotent."""
    global _handler
    root = logging.getLogger()
    root.setLevel(level())
    if not any(getattr(h, "_fanscut_console", False) for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        console._fanscut_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    if data_dir is None or _handler is not None:
        return
    folder = Path(data_dir) / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.TimedRotatingFileHandler(folder / FILE, when="midnight",
                                                      backupCount=LOG_DAYS, encoding="utf-8",
                                                      delay=True)
    except OSError as exc:
        logging.getLogger("fanscut.logs").warning("no log file in %s: %s", folder, exc)
        return
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    _handler = h


def read(data_dir: Path, min_level: str = "DEBUG", search: str = "", skip: int = 0,
         take: int = 100) -> dict[str, Any]:
    """Log entries, newest first, at min_level or above and containing `search`."""
    folder = Path(data_dir) / "logs"
    files = sorted(folder.glob(f"{FILE}*"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    floor = LEVELS.index(min_level.upper()) if min_level.upper() in LEVELS else 0
    needle = search.lower()
    matched: list[dict[str, Any]] = []
    total = 0
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            lv = e.get("level", "INFO")
            if (LEVELS.index(lv) if lv in LEVELS else 1) < floor:
                continue
            if needle and needle not in line.lower():
                continue
            if skip <= total < skip + take:
                matched.append(e)
            total += 1
    return {"results": matched,
            "page_info": {"skip": skip, "take": take, "results": total,
                          "pages": -(-total // take) if take else 0}}


def attach_uvicorn() -> None:
    """Send uvicorn's own logs (startup, errors) to the file too. Called once the
    server is up: uvicorn replaces its loggers' handlers while it starts."""
    if _handler is None:
        return
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        if _handler not in lg.handlers and not lg.propagate:
            lg.addHandler(_handler)

"""Environment checks: ffmpeg, writable dirs, configured sources and tools.

Each check is {"name", "ok", "detail"}; an optional one that is not set up adds
"off": True (ok stays True, so it never fails the whole report).
"""

from __future__ import annotations

import os
import shutil
from typing import Any

from .clients.base import ClientError
from .clients.plex import PlexClient
from .config import Config
from .render import ffmpeg

# Health loads this on every visit: an unreachable server must not stall it.
SERVICE_TIMEOUT = 5.0


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def _ffprobe(cfg: Config) -> dict[str, Any]:
    try:
        out = ffmpeg.run_capture([cfg.ffmpeg.probe_binary, "-version"], 15)
        first = out.decode("utf-8", "replace").splitlines()[0] if out else ""
        return {"name": "ffprobe", "ok": True, "detail": first}
    except Exception:  # noqa: BLE001  # any failure means: not usable
        return {"name": "ffprobe", "ok": False, "detail": f"{cfg.ffmpeg.probe_binary} not found"}


def _nvenc(cfg: Config) -> dict[str, Any]:
    try:
        out = ffmpeg.run_capture([cfg.ffmpeg.binary, "-hide_banner", "-encoders"], 15)
    except Exception:  # noqa: BLE001
        out = b""
    found = [e for e in ("h264_nvenc", "hevc_nvenc") if e.encode() in out]
    detail = ", ".join(found) + " available" if found else "no NVENC encoders in this ffmpeg"
    return {"name": "nvenc", "ok": bool(found), "detail": detail}


def _plex(cfg: Config) -> dict[str, Any]:
    plex = cfg.library.plex
    if not plex.url:
        return {"name": "plex", "ok": True, "off": True, "detail": "not set up"}
    client = PlexClient(plex.url, plex.token, timeout=SERVICE_TIMEOUT)
    client.max_retries = 0
    try:
        sections = client.sections()
    except ClientError as exc:
        return {"name": "plex", "ok": False, "detail": str(exc)}
    names = ", ".join(s["title"] for s in sections if s["type"] in ("movie", "show"))
    detail = f"connected · {names or 'no movie or show libraries'}"
    return {"name": "plex", "ok": True, "detail": detail}


def _effectcraft(cfg: Config) -> dict[str, Any]:
    cli = cfg.render.effectcraft_cli
    if not cli:
        return {"name": "effectcraft", "ok": True, "off": True,
                "detail": "not set · effect segments render in ffmpeg"}
    found = shutil.which(cli) or (os.path.isfile(cli) and os.access(cli, os.X_OK))
    if found:
        return {"name": "effectcraft", "ok": True,
                "detail": f"found · renders effect segments · {cli}"}
    return {"name": "effectcraft", "ok": False, "detail": f"{cli} not found or not executable"}


def run(cfg: Config) -> dict[str, Any]:
    ff = ffmpeg.version(cfg.ffmpeg)
    checks = [
        {"name": "ffmpeg", "ok": ff is not None, "detail": ff or f"{cfg.ffmpeg.binary} not found"},
        _ffprobe(cfg),
        {"name": "data dir", "ok": _writable(cfg.paths.data_dir), "detail": cfg.paths.data_dir},
        {"name": "output dir", "ok": _writable(cfg.paths.output_dir),
         "detail": cfg.paths.output_dir},
    ]
    if cfg.ffmpeg.nvenc and ff:
        checks.append(_nvenc(cfg))
    folder = cfg.library.folder.path
    if folder:
        checks.append({"name": "media folder", "ok": os.path.isdir(folder), "detail": folder})
    checks += [_plex(cfg), _effectcraft(cfg)]
    return {"ok": all(c["ok"] for c in checks), "ffmpeg": ff, "checks": checks}

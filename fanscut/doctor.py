"""Environment checks: ffmpeg, writable dirs, configured sources."""

from __future__ import annotations

import os
from typing import Any

from .config import Config
from .render import ffmpeg


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def run(cfg: Config) -> dict[str, Any]:
    ff = ffmpeg.version(cfg.ffmpeg)
    checks = [
        {"name": "ffmpeg", "ok": ff is not None, "detail": ff or f"{cfg.ffmpeg.binary} not found"},
        {"name": "data dir", "ok": _writable(cfg.paths.data_dir), "detail": cfg.paths.data_dir},
        {"name": "output dir", "ok": _writable(cfg.paths.output_dir),
         "detail": cfg.paths.output_dir},
    ]
    if cfg.ffmpeg.nvenc and ff:
        try:
            out = ffmpeg.run_capture([cfg.ffmpeg.binary, "-hide_banner", "-encoders"], 15)
            has = b"h264_nvenc" in out
        except Exception:  # noqa: BLE001
            has = False
        detail = "h264_nvenc available" if has else "h264_nvenc not in this ffmpeg"
        checks.append({"name": "nvenc", "ok": has, "detail": detail})
    folder = cfg.library.folder.path
    if folder:
        checks.append({"name": "media folder", "ok": os.path.isdir(folder), "detail": folder})
    return {"ok": all(c["ok"] for c in checks), "ffmpeg": ff, "checks": checks}

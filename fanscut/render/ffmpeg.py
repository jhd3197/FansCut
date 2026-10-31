"""ffmpeg/ffprobe wrappers: probe (cached), decode audio, run with progress and cancel."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

from ..config import FFmpegCfg
from ..db import Database, dumps
from ..errors import FFmpegError, JobCancelled

STDERR_TAIL = 6


def _tail(text: str) -> str:
    return " | ".join(text.strip().splitlines()[-STDERR_TAIL:]) or "(no stderr)"


def run_capture(cmd: list[str], timeout: float | None = 300) -> bytes:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise FFmpegError(f"{cmd[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{cmd[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise FFmpegError(
            f"{cmd[0]} exited {proc.returncode}: {_tail(proc.stderr.decode('utf-8', 'replace'))}"
        )
    return proc.stdout


def version(cfg: FFmpegCfg) -> str | None:
    try:
        out = run_capture([cfg.binary, "-version"], timeout=15).decode("utf-8", "replace")
    except FFmpegError:
        return None
    return out.splitlines()[0] if out else None


def _fps(rate: str) -> float:
    try:
        f = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return float(f)


def probe(cfg: FFmpegCfg, path: str) -> dict[str, Any]:
    raw = run_capture(
        [cfg.probe_binary, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         path],
        timeout=120,
    )
    data = json.loads(raw or b"{}")
    info: dict[str, Any] = {"duration": float(data.get("format", {}).get("duration") or 0)}
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and "video" not in info:
            info["video"] = {
                "codec": s.get("codec_name"), "width": s.get("width"), "height": s.get("height"),
                "pix_fmt": s.get("pix_fmt"), "fps": _fps(s.get("avg_frame_rate", "0")),
            }
        elif s.get("codec_type") == "audio" and "audio" not in info:
            info["audio"] = {
                "codec": s.get("codec_name"), "sample_rate": int(s.get("sample_rate") or 0),
                "channels": s.get("channels"),
            }
    return info


class Prober:
    """probe() with results kept in SQLite, keyed by path and invalidated by mtime/size."""

    def __init__(self, cfg: FFmpegCfg, db: Database):
        self.cfg = cfg
        self.db = db

    def __call__(self, path: str) -> dict[str, Any]:
        st = os.stat(path)
        row = self.db.one("SELECT mtime, size, info FROM probe_cache WHERE path = ?", (path,))
        if row and row["mtime"] == st.st_mtime and row["size"] == st.st_size:
            return json.loads(row["info"])
        info = probe(self.cfg, path)
        self.db.execute(
            "INSERT OR REPLACE INTO probe_cache (path, mtime, size, info) VALUES (?,?,?,?)",
            (path, st.st_mtime, st.st_size, dumps(info)),
        )
        return info


def decode_audio(
    cfg: FFmpegCfg, path: str, start: float, duration: float, sample_rate: int
) -> np.ndarray:
    """Mono float32 PCM of [start, start+duration) at `sample_rate`."""
    raw = run_capture(
        [cfg.binary, "-v", "error", "-ss", f"{max(start, 0):.3f}", "-t", f"{duration:.3f}",
         "-i", path, "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        timeout=300,
    )
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def run_with_progress(
    cmd: list[str],
    total_seconds: float,
    on_progress: Callable[[float], None],
    cancel: threading.Event | None = None,
) -> None:
    """Run ffmpeg (cmd must already hold `-progress pipe:1`), reporting 0..1."""
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace"
        )
    except FileNotFoundError as exc:
        raise FFmpegError(f"{cmd[0]} not found") from exc
    assert proc.stdout is not None and proc.stderr is not None
    err_lines: list[str] = []
    drain = threading.Thread(target=lambda: err_lines.extend(proc.stderr or []), daemon=True)
    drain.start()
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise JobCancelled("render cancelled")
        key, _, val = line.strip().partition("=")
        if key in ("out_time_us", "out_time_ms") and val.lstrip("-").isdigit() and total_seconds:
            on_progress(min(max(int(val) / 1_000_000 / total_seconds, 0.0), 1.0))
    proc.wait()
    drain.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise JobCancelled("render cancelled")
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg exited {proc.returncode}: {_tail(''.join(err_lines))}")
    on_progress(1.0)


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

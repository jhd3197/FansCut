"""Execute a RenderPlan: write its work files, run ffmpeg with progress, rename into place."""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable
from pathlib import Path

from . import ffmpeg
from .plan import RenderPlan


def execute(
    plan: RenderPlan,
    workdir: Path,
    on_progress: Callable[[float], None],
    cancel: threading.Event | None = None,
) -> str:
    """Run a plan built for `workdir` (which must exist). Returns the output path."""
    Path(plan.output).parent.mkdir(parents=True, exist_ok=True)
    for name, text in plan.files.items():
        (workdir / name).write_text(text, encoding="utf-8")
    try:
        ffmpeg.run_with_progress(plan.command, plan.total_seconds, on_progress, cancel)
        os.replace(plan.output_tmp, plan.output)
    finally:
        with contextlib.suppress(OSError):
            Path(plan.output_tmp).unlink()
    return plan.output

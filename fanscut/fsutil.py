"""File writes that never leave a half-written file behind."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from pathlib import Path


def atomic_write(path: Path | str, data: str | bytes, *, backup: bool = False,
                 mode: int | None = None) -> None:
    """Write to a temp file beside `path`, then rename it over `path`.

    A crash mid-write leaves the old file untouched. With backup, the previous
    file is copied to "<name>.bak" first."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if backup and p.is_file():
        with contextlib.suppress(OSError):
            shutil.copy2(p, p.with_name(p.name + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):  # best effort; fails quietly on Windows
                os.chmod(tmp, mode)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

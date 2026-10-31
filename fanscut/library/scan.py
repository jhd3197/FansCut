"""Plain media-folder mode: each subfolder is a series, episodes are found by filename."""

from __future__ import annotations

import re
from pathlib import Path

from ..models import Episode, Series

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".webm", ".mov", ".ts"}
_SXXEXX = re.compile(r"[Ss](\d{1,2})[ ._-]?[Ee](\d{1,4})")
_NXNN = re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)")
_ABS = [
    re.compile(r"(?:^|[\s._\[(-])[Ee][Pp]?[ ._-]?(\d{1,4})(?!\d)"),
    re.compile(r"\s-\s(\d{1,4})(?!\d)"),
    re.compile(r"(?:^|[\s._-])(\d{1,4})$"),
]
_YEAR = re.compile(r"\s*\((\d{4})\)\s*$")


def parse_episode(name: str) -> tuple[int, int, bool] | None:
    """(season, number, is_absolute) from a filename, or None."""
    stem = Path(name).stem
    m = _SXXEXX.search(stem) or _NXNN.search(stem)
    if m:
        return int(m.group(1)), int(m.group(2)), False
    for rx in _ABS:
        m = rx.search(stem)
        if m:
            return 1, int(m.group(1)), True
    return None


def clean_title(folder: str) -> tuple[str, int | None]:
    m = _YEAR.search(folder)
    return (folder[: m.start()].strip(), int(m.group(1))) if m else (folder.strip(), None)


def _videos(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXT and p.is_file())


def list_folder_series(root: Path) -> list[Series]:
    if not root.is_dir():
        return []
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        eps = scan_series_dir(d)
        if eps:
            title, year = clean_title(d.name)
            out.append(
                Series(key=f"folder:{d.name}", source="folder", title=title, year=year,
                       episode_count=len(eps))
            )
    return out


def scan_series_dir(d: Path) -> list[Episode]:
    eps: dict[tuple[int, int], Episode] = {}
    for f in _videos(d):
        parsed = parse_episode(f.name)
        if not parsed:
            continue
        season, number, is_abs = parsed
        eps.setdefault(
            (season, number),
            Episode(season=season, number=number, absolute=number if is_abs else None,
                    file=str(f)),
        )
    return [eps[k] for k in sorted(eps)]

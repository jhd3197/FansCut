"""Timeline rendering: recipe + sync report -> per-part cuts in a Plex layout.

Each part becomes one episode file of a new show, so Plex serves the edit
alongside the original:

    <output>/<Series> (<Edit>)/Season 01/<Series> (<Edit>) - S01E01 - <Part>.mkv

with a tvshow.nfo and one episode .nfo per part. Times come from the sync
report when it has a corrected value, else from the recipe.
"""

from __future__ import annotations

import html
from typing import Any

from ..models import Segment
from ..planner import CutOptions, CutPlan
from ..render.plan import safe_name


def show_name(series_title: str, timeline_title: str) -> str:
    """"Attack on Titan" + "… — Chronological" -> "Attack on Titan (Chronological)"."""
    qualifier = timeline_title
    if qualifier.lower().startswith(series_title.lower()):
        qualifier = qualifier[len(series_title):].strip(" —:-·()")
    return safe_name(f"{series_title} ({qualifier})" if qualifier else f"{series_title} timeline")


def corrected_map(report: dict[str, Any] | None) -> dict[tuple[int, int, str], float]:
    """(part, segment, side) -> corrected time, from a sync report."""
    out: dict[tuple[int, int, str], float] = {}
    for src in (report or {}).get("sources", {}).values():
        for b in src.get("boundaries", []):
            out[(b["part"], b["segment"], b["side"])] = b["corrected"]
    return out


def part_segments(doc: dict[str, Any], pi: int,
                  files: dict[str, tuple[str | None, float | None]],
                  corr: dict[tuple[int, int, str], float],
                  skip: set[tuple[int, int]] | None = None
                  ) -> tuple[list[Segment], list[str]]:
    """One part's segments against resolved source files; plus warnings.

    files: {src: (path, duration|None)}; corr: corrected times from the sync
    report; skip: (part, segment) pairs the sync flagged as not in this edition.
    """
    segments, warnings = [], []
    for si, seg in enumerate(doc["parts"][pi]["segments"], start=1):
        if (pi + 1, si) in (skip or set()):
            warnings.append(f"{seg['src']} segment {si}: not in your edition — skipped")
            continue
        resolved = files.get(seg["src"])
        if not resolved or not resolved[0]:
            raise ValueError(f"{seg['src']} has no file in this library")
        path: str = resolved[0]
        duration = resolved[1]
        start = corr.get((pi + 1, si, "start"), seg["start"])
        end = corr.get((pi + 1, si, "end"), seg["end"])
        if duration and end > duration + 5:
            warnings.append(f"{seg['src']} segment {si}: end {end:.1f}s is past the file "
                            f"({duration:.0f}s) — clamped")
            end = min(end, duration)
        if end <= start:
            raise ValueError(f"{seg['src']} segment {si}: corrected range is empty")
        segments.append(Segment(seg["src"], path, round(start, 3), round(end, 3),
                                seg.get("desc") or seg["src"],
                                xfade_ms=float(seg.get("xfade_ms") or 0)))
    return segments, warnings


def part_cut(doc: dict[str, Any], pi: int, files: dict[str, tuple[str | None, float | None]],
             corr: dict[tuple[int, int, str], float],
             skip: set[tuple[int, int]] | None = None) -> tuple[CutPlan, list[str]]:
    segments, warnings = part_segments(doc, pi, files, corr, skip)
    kept = sorted({s.episode_id for s in segments})
    total = sum(s.duration for s in segments)
    return CutPlan(segments, kept, [], 0.0, total, CutOptions()), warnings


def part_filename(show: str, pi: int, part_title: str, ext: str) -> str:
    return f"{show} - S01E{pi:02d} - {safe_name(part_title)}.{ext}"


def nfo_show(title: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<tvshow>\n'
            f"  <title>{html.escape(title)}</title>\n</tvshow>\n")


def nfo_episode(title: str, ep_number: int, season: int = 1) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<episodedetails>\n'
            f"  <title>{html.escape(title)}</title>\n"
            f"  <season>{season}</season>\n  <episode>{ep_number}</episode>\n"
            f"</episodedetails>\n")


def nfo_movie(title: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<movie>\n'
            f"  <title>{html.escape(title)}</title>\n</movie>\n")

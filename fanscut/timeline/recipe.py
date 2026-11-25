"""Timeline recipes: media-free chronological fan edits.

A timeline recipe pulls any range from any episode, in any order, grouped
into output parts. It never holds video, audio, subtitles or images: only
source keys (Plex/TVDB numbering, SxxEyy with specials as S00Exx), times in
seconds, ordering, and lossy cut-point anchors.

    {"schema": 2, "kind": "timeline", "title": "...", "series": {...},
     "sources": {"S04E21": {"duration": 1420.3, "note": "..."}},
     "parts": [{"title": "Year -1003", "segments": [
         {"src": "S04E21", "start": 312.4, "end": 498.0,
          "anchors": {"start": {"a": "a1:...", "v": "v1:..."}, "end": {...}}}]}],
     "credits": {"author": "...", "based_on": "...", "license": "..."}}

Everything here is untrusted input: validate() is strict and complete.
"""

from __future__ import annotations

import base64
import math
import re
from typing import Any

from ..errors import FansCutError

SCHEMA = 2
KIND = "timeline"
MAX_BYTES = 2_000_000
MAX_PARTS = 200
MAX_SEGMENTS = 5_000
MAX_SOURCES = 2_000
MAX_TITLE = 200
MAX_NOTE = 500
SRC_RE = re.compile(r"^S\d{2}E\d{2,3}$")
ANCHOR_RE = re.compile(r"^[av]1:[A-Za-z0-9+/]+={0,2}$")
TOLERANCE = 5.0  # a cut may point up to this far past the stated duration


class TimelineError(FansCutError):
    pass


def _text(v: Any, what: str, limit: int, required: bool = True) -> str:
    if not isinstance(v, str) or any(ord(c) < 32 for c in v):
        raise TimelineError(f"{what}: expected plain text")
    v = v.strip()
    if required and not v:
        raise TimelineError(f"{what}: must not be empty")
    if len(v) > limit:
        raise TimelineError(f"{what}: over {limit} characters")
    return v


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise TimelineError(f"{what}: expected a number")
    v = float(v)
    if not math.isfinite(v):
        raise TimelineError(f"{what}: not a finite number")
    return v


def _anchors(v: Any, what: str) -> dict[str, dict[str, str]]:
    if not isinstance(v, dict):
        raise TimelineError(f"{what}: expected an object")
    out = {}
    for side in ("start", "end"):
        a = v.get(side)
        if a is None:
            continue
        if not isinstance(a, dict):
            raise TimelineError(f"{what}.{side}: expected an object")
        clean = {}
        for kind in ("a", "v"):
            val = a.get(kind)
            if val is None:
                continue
            if not isinstance(val, str) or not ANCHOR_RE.match(val):
                raise TimelineError(f"{what}.{side}.{kind}: bad anchor (want a1:/v1: base64)")
            try:
                base64.b64decode(val[3:], validate=True)
            except ValueError as exc:
                raise TimelineError(f"{what}.{side}.{kind}: invalid base64") from exc
            clean[kind] = val
        if clean:
            out[side] = clean
    return out


def validate(doc: Any) -> dict[str, Any]:
    """Return a cleaned timeline recipe or raise TimelineError."""
    if not isinstance(doc, dict):
        raise TimelineError("expected an object")
    if doc.get("schema") != SCHEMA or doc.get("kind") != KIND:
        raise TimelineError(f"not a schema {SCHEMA} timeline recipe")
    title = _text(doc.get("title"), "title", MAX_TITLE)
    series = doc.get("series")
    if not isinstance(series, dict):
        raise TimelineError("series must be an object")
    clean_series: dict[str, Any] = {"title": _text(series.get("title"), "series.title", MAX_TITLE)}
    for k in ("tvdb_id", "anilist_id", "mal_id"):
        v = series.get(k)
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                raise TimelineError(f"series.{k}: expected a non-negative integer")
            clean_series[k] = v

    raw_sources = doc.get("sources")
    if not isinstance(raw_sources, dict) or not raw_sources:
        raise TimelineError("sources must be a non-empty object")
    if len(raw_sources) > MAX_SOURCES:
        raise TimelineError(f"over {MAX_SOURCES} sources")
    sources = {}
    for src, s in raw_sources.items():
        if not isinstance(src, str) or not SRC_RE.match(src):
            raise TimelineError(f"bad source key {src!r} (want SxxEyy)")
        if not isinstance(s, dict):
            raise TimelineError(f"sources.{src}: expected an object")
        dur = _num(s.get("duration"), f"sources.{src}.duration")
        if dur <= 0:
            raise TimelineError(f"sources.{src}.duration: must be positive")
        entry: dict[str, Any] = {"duration": dur}
        if s.get("note") is not None:
            entry["note"] = _text(s["note"], f"sources.{src}.note", MAX_NOTE, required=False)
        sources[src] = entry

    parts_raw = doc.get("parts")
    if not isinstance(parts_raw, list) or not parts_raw:
        raise TimelineError("parts must be a non-empty list")
    if len(parts_raw) > MAX_PARTS:
        raise TimelineError(f"over {MAX_PARTS} parts")
    total_segments = 0
    parts = []
    for pi, p in enumerate(parts_raw, start=1):
        if not isinstance(p, dict):
            raise TimelineError(f"part {pi}: expected an object")
        ptitle = _text(p.get("title"), f"part {pi} title", MAX_TITLE)
        segs_raw = p.get("segments")
        if not isinstance(segs_raw, list) or not segs_raw:
            raise TimelineError(f"part {pi}: segments must be a non-empty list")
        segs = []
        for si, seg in enumerate(segs_raw, start=1):
            what = f"part {pi} segment {si}"
            if not isinstance(seg, dict):
                raise TimelineError(f"{what}: expected an object")
            src = seg.get("src")
            if src not in sources:
                raise TimelineError(f"{what}: unknown src {src!r}")
            start = _num(seg.get("start"), f"{what}.start")
            end = _num(seg.get("end"), f"{what}.end")
            if start < 0:
                raise TimelineError(f"{what}.start: negative")
            if not end > start:
                raise TimelineError(f"{what}: end must be after start")
            if end > sources[src]["duration"] + TOLERANCE:
                raise TimelineError(f"{what}: end past the source duration")
            clean_seg: dict[str, Any] = {"src": src, "start": start, "end": end}
            if seg.get("anchors") is not None:
                clean_seg["anchors"] = _anchors(seg["anchors"], f"{what}.anchors")
            if seg.get("desc") is not None:
                clean_seg["desc"] = _text(seg["desc"], f"{what}.desc", MAX_NOTE, required=False)
            segs.append(clean_seg)
        total_segments += len(segs)
        if total_segments > MAX_SEGMENTS:
            raise TimelineError(f"over {MAX_SEGMENTS} segments in total")
        parts.append({"title": ptitle, "segments": segs})

    credits = doc.get("credits")
    clean_credits = {}
    if credits is not None:
        if not isinstance(credits, dict):
            raise TimelineError("credits must be an object")
        for k, v in credits.items():
            clean_credits[_text(k, "credits key", 50)] = _text(v, f"credits.{k}", MAX_NOTE)
    out: dict[str, Any] = {"schema": SCHEMA, "kind": KIND, "title": title,
                           "series": clean_series, "sources": sources, "parts": parts}
    if clean_credits:
        out["credits"] = clean_credits
    return out


def missing_sources(doc: dict[str, Any], available: set[str]) -> list[dict[str, Any]]:
    """Sources the library lacks, with the 1-based parts that need them."""
    need: dict[str, list[int]] = {}
    for pi, part in enumerate(doc["parts"], start=1):
        for seg in part["segments"]:
            need.setdefault(seg["src"], [])
            if pi not in need[seg["src"]]:
                need[seg["src"]].append(pi)
    return [{"src": src, "parts": ps, "note": doc["sources"][src].get("note", "")}
            for src, ps in sorted(need.items()) if src not in available]

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
from . import effects

SCHEMA = 2
KIND = "timeline"
MAX_BYTES = 2_000_000
MAX_PARTS = 200
MAX_SEGMENTS = 5_000
MAX_SOURCES = 2_000
MAX_TITLE = 200
MAX_NOTE = 500
MAX_XFADE_MS = 10_000
SRC_RE = re.compile(r"^S\d{2}E\d{2,3}$")
MOVIE_RE = re.compile(r"^M\d{1,10}$")  # M + TMDB id
ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,15}$")  # short movie alias: AUJ, DOS, BOTFA
ANCHOR_RE = re.compile(r"^[av]1:[A-Za-z0-9+/]+={0,2}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_TOGGLES = 50
MAX_VARIANTS = 20
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


def _toggles(raw: Any, sources: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if not isinstance(raw, list) or len(raw) > MAX_TOGGLES:
        raise TimelineError(f"toggles: expected a list of at most {MAX_TOGGLES}")
    out: dict[str, dict[str, Any]] = {}
    for i, t in enumerate(raw, start=1):
        if not isinstance(t, dict):
            raise TimelineError(f"toggle {i}: expected an object")
        tid = t.get("id")
        if not isinstance(tid, str) or not ID_RE.match(tid):
            raise TimelineError(f"toggle {i}.id: lowercase letters, digits and '-' only")
        if tid in out:
            raise TimelineError(f"toggle {tid}: duplicate id")
        default = t.get("default", False)
        if not isinstance(default, bool):
            raise TimelineError(f"toggle {tid}.default: expected true or false")
        needs = t.get("needs", [])
        if not isinstance(needs, list) or any(not isinstance(n, str) or n not in sources
                                              for n in needs):
            raise TimelineError(f"toggle {tid}.needs: a list of source keys from sources")
        out[tid] = {"id": tid, "label": _text(t.get("label"), f"toggle {tid}.label", MAX_TITLE),
                    "default": default, "needs": list(dict.fromkeys(needs))}
    return out


def _variants(raw: Any, toggles: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_VARIANTS:
        raise TimelineError(f"variants: expected a list of at most {MAX_VARIANTS}")
    out, seen = [], set()
    for i, v in enumerate(raw, start=1):
        if not isinstance(v, dict):
            raise TimelineError(f"variant {i}: expected an object")
        vid = v.get("id")
        if not isinstance(vid, str) or not ID_RE.match(vid) or vid in seen:
            raise TimelineError(f"variant {i}.id: a unique id of lowercase letters, digits, '-'")
        seen.add(vid)
        vals = v.get("toggles", {})
        if not isinstance(vals, dict):
            raise TimelineError(f"variant {vid}.toggles: expected an object of toggle id: bool")
        for k, on in vals.items():
            if k not in toggles or not isinstance(on, bool):
                raise TimelineError(f"variant {vid}.toggles.{k}: unknown toggle or not a bool")
        out.append({"id": vid, "label": _text(v.get("label"), f"variant {vid}.label", 60),
                    "toggles": dict(vals)})
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
    if not isinstance(raw_sources, dict):
        raise TimelineError("sources must be an object")  # may be empty: an editor draft
    if len(raw_sources) > MAX_SOURCES:
        raise TimelineError(f"over {MAX_SOURCES} sources")
    sources = {}
    for src, s in raw_sources.items():
        if not isinstance(src, str) or not (SRC_RE.match(src) or MOVIE_RE.match(src)
                                            or ALIAS_RE.match(src)):
            raise TimelineError(f"bad source key {src!r} (want SxxEyy, M<tmdb id> or an "
                                "upper-case movie alias like AUJ)")
        if not isinstance(s, dict):
            raise TimelineError(f"sources.{src}: expected an object")
        kind = s.get("kind", "episode")
        if kind == "movie":
            if SRC_RE.match(src):
                raise TimelineError(f"sources.{src}: an episode key can't be a movie")
            tmdb = s.get("tmdb")
            if isinstance(tmdb, bool) or not isinstance(tmdb, int) or tmdb <= 0:
                raise TimelineError(f"sources.{src}.tmdb: expected a positive integer")
            if MOVIE_RE.match(src) and src != f"M{tmdb}":
                raise TimelineError(f"sources.{src}: key and tmdb disagree")
        elif kind != "episode":
            raise TimelineError(f"sources.{src}.kind: want episode | movie")
        elif not SRC_RE.match(src):
            raise TimelineError(f"sources.{src}: episode sources key on SxxEyy")
        entry: dict[str, Any] = {}
        if s.get("duration") is not None or kind != "movie":
            # movies may leave it out: the user's copy supplies it on import
            dur = _num(s.get("duration"), f"sources.{src}.duration")
            if dur <= 0:
                raise TimelineError(f"sources.{src}.duration: must be positive")
            entry["duration"] = dur
        if kind == "movie":
            entry["kind"] = "movie"
            entry["tmdb"] = s["tmdb"]
            if s.get("edition") is not None:
                entry["edition"] = _text(s["edition"], f"sources.{src}.edition", 50,
                                         required=False)
        note = s.get("note") if s.get("note") is not None else s.get("title")
        if note is not None:
            entry["note"] = _text(note, f"sources.{src}.note", MAX_NOTE, required=False)
        if s.get("optional") is not None:
            if not isinstance(s["optional"], bool):
                raise TimelineError(f"sources.{src}.optional: expected true or false")
            if s["optional"]:
                entry["optional"] = True
        sources[src] = entry

    toggles = _toggles(doc.get("toggles"), sources)

    parts_raw = doc.get("parts")
    if not isinstance(parts_raw, list):
        raise TimelineError("parts must be a list")  # may be empty: an editor draft
    if len(parts_raw) > MAX_PARTS:
        raise TimelineError(f"over {MAX_PARTS} parts")
    total_segments = 0
    parts = []
    for pi, p in enumerate(parts_raw, start=1):
        if not isinstance(p, dict):
            raise TimelineError(f"part {pi}: expected an object")
        ptitle = _text(p.get("title"), f"part {pi} title", MAX_TITLE)
        segs_raw = p.get("segments")
        if not isinstance(segs_raw, list):
            raise TimelineError(f"part {pi}: segments must be a list")
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
            if "duration" in sources[src] and end > sources[src]["duration"] + TOLERANCE:
                raise TimelineError(f"{what}: end past the source duration")
            clean_seg: dict[str, Any] = {"src": src, "start": start, "end": end}
            if seg.get("xfade_ms") is not None:
                xf = _num(seg["xfade_ms"], f"{what}.xfade_ms")
                if not 0 < xf <= MAX_XFADE_MS:
                    raise TimelineError(f"{what}.xfade_ms: 0–{MAX_XFADE_MS}")
                clean_seg["xfade_ms"] = xf
            if seg.get("anchors") is not None:
                clean_seg["anchors"] = _anchors(seg["anchors"], f"{what}.anchors")
            if seg.get("desc") is not None:
                clean_seg["desc"] = _text(seg["desc"], f"{what}.desc", MAX_NOTE, required=False)
            try:
                clean_seg.update(effects.validate(seg, what, sources))
            except effects.EffectError as exc:
                raise TimelineError(str(exc)) from exc
            when = seg.get("when")
            if when is not None:
                if not isinstance(when, str) or when.lstrip("!") not in toggles:
                    raise TimelineError(f"{what}.when: unknown toggle {when!r}")
                clean_seg["when"] = when
            if sources[src].get("optional"):
                if not when or when.startswith("!"):
                    raise TimelineError(f"{what}: optional source {src} needs a toggle "
                                        '("when": "<toggle id>")')
                if src not in toggles[when]["needs"]:
                    toggles[when]["needs"].append(src)  # a toggle needs what it shows
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
    if toggles:
        out["toggles"] = list(toggles.values())
    variants = _variants(doc.get("variants"), toggles)
    if variants:
        out["variants"] = variants
    if clean_credits:
        out["credits"] = clean_credits
    if doc.get("poster") is not None:  # a link only; downloaded locally on install
        poster = _text(doc["poster"], "poster", 500)
        if not poster.startswith("https://"):
            raise TimelineError("poster: want an https URL")
        out["poster"] = poster
    for k, limit in (("category", 40), ("tagline", 80), ("id", 120), ("version", 40)):
        if doc.get(k) is not None:
            out[k] = _text(doc[k], k, limit, required=False)
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

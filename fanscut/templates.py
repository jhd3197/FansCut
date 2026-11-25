"""Built-in cut templates: a starting fanscut.json + README.md + outline.md.

    trilogy-merge   two or more films into one (one part, every film in order)
    watch-order     films as parts, in the order you pick (a playlist or a show)
    chronological   a series re-ordered by story time (one part per season to start)
    canon           a series without filler (canon and mixed episodes, whole)

The output is the same repo shape as published cuts (jhd3197/took): the
recipe at the root, a README for people and an outline for the editor. It
holds no media: sources are TMDB ids / episode keys with the user's own
durations, ready to refine in the editor and share.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import FansCutError

TEMPLATES = [
    {"id": "trilogy-merge", "label": "Trilogy merge", "kind": "merge", "needs": "movies",
     "description": "Two or more films cut into one. Starts with every film whole, in order."},
    {"id": "watch-order", "label": "Watch order", "kind": "order", "needs": "movies",
     "description": "Films in the order you choose, one part each. Install as a Plex "
                    "playlist or render as a show."},
    {"id": "chronological", "label": "Chronological", "kind": "chronological",
     "needs": "series",
     "description": "A show re-ordered by story time. Starts as one part per season; move "
                    "scenes in the editor."},
    {"id": "canon", "label": "Canon cut", "kind": "canon", "needs": "series",
     "description": "A show without filler: canon and mixed episodes, whole."},
]
SLUG_RE = re.compile(r"[^a-z0-9]+")


def get(tid: str) -> dict[str, Any]:
    t = next((t for t in TEMPLATES if t["id"] == tid), None)
    if t is None:
        raise FansCutError(f"unknown template {tid!r}")
    return t


def slug(title: str) -> str:
    return SLUG_RE.sub("-", title.lower()).strip("-")[:60] or "cut"


def alias(title: str, used: set[str]) -> str:
    """'The Desolation of Smaug' -> 'DOS' (unique, upper-case, recipe-legal)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", title)
             if w.lower() not in {"the", "of", "a", "an", "and"}] or ["SRC"]
    base = "".join(w[0] for w in words).upper()
    base = base if len(base) >= 2 else (words[0][:3].upper() + "X")[:3]
    if not base[0].isalpha():
        base = "M" + base
    out, n = base[:14], 2
    while out in used:
        out, n = f"{base[:12]}{n}", n + 1
    used.add(out)
    return out


def movie_doc(template: dict[str, Any], title: str, films: list[dict[str, Any]],
              author: str = "") -> dict[str, Any]:
    """films: [{tmdb, title, year, duration, edition?}] in the chosen order."""
    if len(films) < (2 if template["id"] == "trilogy-merge" else 1):
        raise FansCutError("pick at least two films" if template["id"] == "trilogy-merge"
                           else "pick at least one film")
    used: set[str] = set()
    sources, segs, parts = {}, [], []
    for f in films:
        if not f.get("duration"):
            raise FansCutError(f"{f['title']}: no runtime known (is the file on disk?)")
        key = alias(f["title"], used)
        src: dict[str, Any] = {"kind": "movie", "tmdb": f["tmdb"], "note": f["title"],
                               "duration": round(float(f["duration"]), 3)}
        if f.get("edition"):
            src["edition"] = f["edition"]
        sources[key] = src
        seg = {"src": key, "start": 0.0, "end": src["duration"], "desc": f["title"]}
        segs.append(seg)
        parts.append({"title": f["title"], "segments": [seg]})
    doc: dict[str, Any] = {
        "schema": 2, "kind": "timeline", "title": title, "series": {"title": title},
        "category": "Merges" if template["id"] == "trilogy-merge" else "Watch in story order",
        "tagline": (f"{len(films)} films, as one" if template["id"] == "trilogy-merge"
                    else "in story order"),
        "sources": sources,
        "parts": [{"title": title, "segments": segs}] if template["id"] == "trilogy-merge"
        else parts,
        "credits": {"author": author or "you", "based_on": f"FansCut template: {template['label']}",
                    "license": "CC-BY-4.0"}}
    return doc


def series_doc(template: dict[str, Any], title: str, series: dict[str, Any],
               episodes: list[dict[str, Any]], author: str = "") -> dict[str, Any]:
    """episodes: [{id, season, title, duration, label?}] in aired order."""
    keep = [e for e in episodes if e.get("duration")]
    if template["id"] == "canon":
        keep = [e for e in keep if e.get("label") in ("canon", "mixed")]
    if not keep:
        raise FansCutError("no episodes with a known runtime"
                           + (" and a canon/mixed label (match the show first)"
                              if template["id"] == "canon" else ""))
    sources = {e["id"]: {"duration": round(float(e["duration"]), 3),
                         **({"note": e["title"]} if e.get("title") else {})} for e in keep}
    by_season: dict[int, list[dict[str, Any]]] = {}
    for e in keep:
        by_season.setdefault(e["season"], []).append(
            {"src": e["id"], "start": 0.0, "end": sources[e["id"]]["duration"],
             **({"desc": e["title"]} if e.get("title") else {})})
    parts = [{"title": "Specials" if s == 0 else f"Season {s}", "segments": segs}
             for s, segs in sorted(by_season.items())]
    ser = {"title": series["title"]}
    if series.get("tvdb_id"):
        ser["tvdb_id"] = series["tvdb_id"]
    return {"schema": 2, "kind": "timeline", "title": title, "series": ser,
            "category": "Anime" if series.get("is_anime") else "Shows",
            "tagline": "in story order" if template["id"] == "chronological" else "no filler",
            "sources": sources, "parts": parts,
            "credits": {"author": author or "you",
                        "based_on": f"FansCut template: {template['label']}",
                        "license": "CC-BY-4.0"}}


def files(template: dict[str, Any], doc: dict[str, Any]) -> dict[str, str]:
    """README.md, fanscut.json and outline.md for a new cut repo."""
    srcs = doc["sources"]
    lines = []
    for k, s in srcs.items():
        what = s.get("note") or k
        ed = f" ({s['edition']})" if s.get("edition") else ""
        ident = f"TMDB {s['tmdb']}" if s.get("kind") == "movie" else k
        lines.append(f"- `{k}` {what}{ed} — {ident}")
    runtime = sum(seg["end"] - seg["start"] for p in doc["parts"] for seg in p["segments"])
    readme = f"""# {doc['title']}

{template['description']}

A [FansCut](https://github.com/jhd3197/FansCut) cut. This repo holds the recipe
only: timestamps, ordering and fingerprints. No video, audio, subtitles or
artwork. You need your own copies of:

{chr(10).join(lines)}

## Use it

In FansCut: Discover → **Add by repo URL** → paste this repo's URL. FansCut
checks your copies, finds the cut points in them and builds the cut on your
machine.

Runtime as started: {round(runtime / 60)} min · template: {template['label']}.

Credits: {doc['credits'].get('author', '')} · license {doc['credits'].get('license', '')}.
"""
    outline = [f"# {doc['title']} — outline", "",
               "Plan the cut here, then refine it in the FansCut editor. One line per",
               "segment: source, in, out, what happens.", ""]
    for p in doc["parts"]:
        outline.append(f"## {p['title']}")
        for seg in p["segments"]:
            outline.append(f"- {seg['src']} {seg['start']:.0f}–{seg['end']:.0f}s "
                           f"{seg.get('desc', '')}".rstrip())
        outline.append("")
    return {"README.md": readme, "fanscut.json": json.dumps(doc, indent=2, ensure_ascii=False)
            + "\n", "outline.md": "\n".join(outline)}

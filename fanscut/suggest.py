"""First-run suggestions: what this library is one click away from.

Three sources of ideas:
  a. community timeline recipes (JSON files in <data_dir>/recipes), matched by
     TMDB/TVDB id and edition;
  b. Plex collection tags: own two or more films of a collection and FansCut
     offers a merge (one render) and a watch-order playlist (no render);
  c. shows with known filler episodes get a "canon cut" suggestion.

Everything is a plain dict so the API and UI stay thin. `name` is what the
tile shows (the collection or show, without "— merged movie"); `art` lists the
library keys whose posters the tile uses (/api/art/poster/<key>). Status:
  ready    every source resolves
  partly   some sources resolve, some are missing (named in `missing`)
  missing  nothing resolves
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .art import art_ref

# resolve(src, source_entry) -> (file, duration, error_reason | None)
Resolver = Callable[[str, dict[str, Any]], tuple[str | None, float | None, str | None]]


def _status(n_sources: int, missing: list[dict[str, str]]) -> str:
    """A missing audio language leaves the source usable; a missing file does not."""
    if not missing:
        return "ready"
    fatal = [m for m in missing if "audio track" not in m["reason"]]
    if len(fatal) >= n_sources:
        return "missing"
    return "partly"


def from_recipe(name: str, doc: dict[str, Any], resolve: Resolver,
                art: list[str] | None = None, keys: list[str] | None = None) -> dict[str, Any]:
    """A community recipe against the library; `art` = art refs of its sources.

    Every suggestion carries `keys`: the library keys of the movies and shows it
    uses, so a movie or show page can list the suggestions that include it."""
    missing = []
    for src, entry in doc.get("sources", {}).items():
        _file, _dur, err = resolve(src, entry)
        if err:
            missing.append({"src": src, "reason": err})
    n_seg = sum(len(p["segments"]) for p in doc.get("parts", []))
    return {"id": f"recipe:{name}", "kind": "recipe", "title": doc.get("title", name),
            "name": doc.get("title", name), "art": list(dict.fromkeys(art or [])),
            "keys": list(dict.fromkeys(keys or [])),
            "category": doc.get("category") or "Community cuts",
            "tagline": doc.get("tagline", ""),
            "status": _status(len(doc.get("sources", {})), missing), "missing": missing,
            "detail": {"parts": len(doc.get("parts", [])), "segments": n_seg,
                       "credits": doc.get("credits", {})}}


def from_collection(name: str, movies: list[Any], lang_missing: list[str]) -> list[dict[str, Any]]:
    """Merge + watch-order templates for a Plex collection of 2+ owned movies."""
    years = sorted(m.year or 0 for m in movies)
    out = []
    missing = [{"src": t, "reason": "no preferred audio language"} for t in lang_missing]
    out.append({
        "id": f"merge:{name}", "kind": "collection_merge", "category": "Merges",
        "name": name, "art": [art_ref(m) for m in movies],
        "keys": [m.key for m in movies],
        "tagline": f"{name}, as one film",
        "title": f"{name} — merged movie",
        "status": "ready" if not missing else "partly", "missing": missing,
        "detail": {"movies": [m.title for m in movies], "years": years,
                   "template": "one film from every entry, in release order"},
    })
    out.append({
        "id": f"order:{name}", "kind": "watch_order", "category": "Watch in story order",
        "name": name, "art": [art_ref(m) for m in movies],
        "keys": [m.key for m in movies],
        "tagline": "in release order",
        "title": f"{name} — watch order playlist",
        "status": "ready", "missing": [],
        "detail": {"movies": [m.title for m in movies],
                   "template": "Plex playlist in release order; nothing is rendered"},
    })
    return out


def canon_cut(series_title: str, key: str, n_filler: int, n_episodes: int,
              files_missing: list[str], lang_missing: list[str],
              art: str | None = None) -> dict[str, Any] | None:
    """A 'canon cut' suggestion for a matched show with known filler."""
    if n_filler <= 0:
        return None
    missing = [{"src": e, "reason": "no file"} for e in files_missing]
    missing += [{"src": e, "reason": "no preferred audio language"} for e in lang_missing]
    n = max(n_episodes, 1)
    return {"id": f"canon:{key}", "kind": "canon_cut", "category": "Anime",
            "name": series_title, "art": [art or key], "keys": [key],
            "tagline": "no filler",
            "title": f"{series_title} — canon cut",
            "status": _status(n, missing) if missing else "ready",
            "missing": missing,
            "detail": {"series_key": key, "filler_episodes": n_filler,
                       "episodes": n_episodes}}

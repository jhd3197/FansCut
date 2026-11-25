"""Read models for the UI: movies, collections, details and "my cuts".

Everything here is derived from the library, the probe cache, the last
library check and the timelines table. Nothing writes, and listing never
probes (detail pages do, and the probe is cached).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .art import art_ref
from .errors import NotFoundError
from .library import check, known

if TYPE_CHECKING:
    from .core import Core
    from .models import Movie


def _edition(m: Movie) -> str | None:
    return check.edition_from_name(Path(m.file).name) if m.file else None


def _collections_of(m: Movie) -> list[str]:
    """Plex collection tags plus the known set (The Hobbit, The Lord of the Rings…)."""
    out = list(m.collections)
    k = known.movie(m.tmdb_id, m.title, m.year)
    if k and k["set"] not in out:
        out.append(k["set"])
    return out


def _health_by_file(core: Core) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in (core.last_check() or {}).get("findings", []):
        if f.get("file"):
            out.setdefault(f["file"], []).append(f)
    return out


def movie_row(m: Movie, health: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    problems = [f for f in health.get(m.file or "", []) if f["severity"] in ("error", "warn")
                and f["check"] != "mismatch"]
    return {"key": m.key, "title": m.title, "year": m.year, "tmdb_id": m.tmdb_id,
            "source": m.source, "file": m.file, "on_disk": bool(m.file),
            "minutes": round(m.duration / 60) if m.duration else None,
            "edition": _edition(m), "collections": _collections_of(m),
            "problem": problems[0]["message"] if problems else None,
            "problem_severity": problems[0]["severity"] if problems else None,
            "art": art_ref(m)}


def movies(core: Core) -> dict[str, Any]:
    ms, errors = core.library.list_movies()
    health = _health_by_file(core)
    return {"errors": errors, "movies": [movie_row(m, health) for m in ms]}


def collections(core: Core) -> dict[str, Any]:
    ms, _ = core.library.list_movies()
    groups: dict[str, list[Movie]] = {}
    for m in ms:
        for c in _collections_of(m):
            groups.setdefault(c, []).append(m)
    cuts = all_cuts(core)["cuts"]
    out = []
    for name, members in sorted(groups.items()):
        tmdbs = {m.tmdb_id for m in members if m.tmdb_id}
        n_cuts = sum(1 for c in cuts if tmdbs & set(c.get("tmdb_ids", [])))
        by_year = sorted(members, key=lambda m: m.year or 0)
        out.append({"name": name, "films": len(members), "cuts": n_cuts,
                    "years": sorted({m.year for m in members if m.year}),
                    # poster and backdrop refs, first film first (an https URL or a key)
                    "art": [art_ref(m) for m in by_year],
                    "backdrops": [art_ref(m, "backdrop") for m in by_year]})
    return {"collections": out}


def collection(core: Core, name: str) -> dict[str, Any]:
    ms, _ = core.library.list_movies()
    health = _health_by_file(core)
    members = sorted([m for m in ms if name in _collections_of(m)],
                     key=lambda m: (m.year or 0, m.title))
    if not members:
        raise NotFoundError(f"collection {name!r} not found")
    tmdbs = {m.tmdb_id for m in members if m.tmdb_id}
    total = sum(m.duration or 0 for m in members)
    sugg = [s for s in core.suggestions()["suggestions"]
            if s["id"].partition(":")[2] == name or name.lower() in s["title"].lower()]
    cuts = [c for c in all_cuts(core)["cuts"] if tmdbs & set(c.get("tmdb_ids", []))]
    return {"name": name, "films": [movie_row(m, health) for m in members],
            "minutes": round(total / 60), "suggestions": sugg, "cuts": cuts}


def movie(core: Core, key: str) -> dict[str, Any]:
    ms, _ = core.library.list_movies()
    m = next((x for x in ms if x.key == key), None) or core.library.get_movie(key)
    health = _health_by_file(core)
    row = movie_row(m, health)
    info: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = []
    if m.file and Path(m.file).is_file():
        findings, edition, info = core.checker.check_file(m.file, m.title, m.year, m.tmdb_id)
        row["edition"] = edition or row["edition"]
    siblings = []
    for c in row["collections"]:
        siblings.append({"name": c, "films": [movie_row(x, health) for x in sorted(
            (x for x in ms if c in _collections_of(x)), key=lambda x: (x.year or 0, x.title))]})
    cuts = [c for c in all_cuts(core)["cuts"] if m.tmdb_id and m.tmdb_id in c["tmdb_ids"]]
    return {**row, "probe": info, "findings": findings + health.get(m.file or "", []),
            "collection_films": siblings, "cuts": cuts}


def episode(core: Core, key: str, episode_id: str) -> dict[str, Any]:
    eps = core.library.episodes(key)
    e = next((x for x in eps if x.id == episode_id), None)
    if e is None:
        raise NotFoundError(f"{episode_id} not found")
    ids = [x.id for x in eps]
    i = ids.index(episode_id)
    info = None
    if e.file and Path(e.file).is_file():
        try:
            info = core.prober(e.file)
        except Exception:  # noqa: BLE001 - shown as "unreadable"
            info = None
    cuts = [c for c in all_cuts(core)["cuts"]
            if c["series_key"] == key and episode_id in c.get("episode_ids", [])]
    return {"id": e.id, "season": e.season, "number": e.number, "title": e.title,
            "file": e.file, "duration": e.duration, "air_date": e.air_date, "probe": info,
            "prev": ids[i - 1] if i > 0 else None,
            "next": ids[i + 1] if i + 1 < len(ids) else None, "cuts": cuts}


def all_cuts(core: Core) -> dict[str, Any]:
    """Every timeline plus every render: the "My cuts" list."""
    cuts = []
    installs = {i["timeline_id"]: f"https://github.com/{i['repo']}"
                for i in core.db.query("SELECT timeline_id, repo FROM installs")}
    for r in core.db.query("SELECT * FROM timelines ORDER BY updated_at DESC"):
        doc = json.loads(r["doc"])
        srcs = doc.get("sources", {})
        seconds = sum(s["end"] - s["start"] for p in doc["parts"] for s in p["segments"])
        cuts.append({
            "kind": "timeline", "id": r["id"], "series_key": r["series_key"],
            "title": r["title"], "parts": len(doc["parts"]),
            "segments": sum(len(p["segments"]) for p in doc["parts"]),
            "minutes": round(seconds / 60), "variants": len(doc.get("variants", [])),
            "tmdb_ids": [s["tmdb"] for s in srcs.values() if s.get("kind") == "movie"],
            "episode_ids": [k for k, s in srcs.items() if s.get("kind", "episode") == "episode"],
            "credits": doc.get("credits", {}), "updated_at": r["updated_at"],
            "version": doc.get("version") or doc.get("credits", {}).get("version", ""),
            "category": doc.get("category", ""), "tagline": doc.get("tagline", ""),
            "repo": installs.get(r["id"], ""),
            "synced": core.db.one("SELECT 1 FROM timeline_syncs WHERE timeline_id = ?",
                                  (r["id"],)) is not None})
    renders = core.list_renders()
    return {"cuts": cuts, "renders": renders}

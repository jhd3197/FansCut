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
    from .models import Movie, Series


def _edition(m: Movie) -> str | None:
    return check.edition_from_name(Path(m.file).name) if m.file else None


def _collections_of(m: Movie) -> list[str]:
    """Plex collection tags plus the known set (The Hobbit, The Lord of the Rings…)."""
    out = list(m.collections)
    k = known.movie(m.tmdb_id, m.title, m.year)
    if k and k["set"] not in out:
        out.append(k["set"])
    return out


def _franchise(name: str) -> str:
    """"The Hobbit Collection" -> "the hobbit": what a show's title must equal to join."""
    n = known.norm(name)
    return n[:-len(" collection")] if n.endswith(" collection") else n


def _show_members(core: Core, names: list[str]) -> dict[str, list[Series]]:
    """Shows in each collection: Plex's own show collections, or a show whose title is
    the collection's franchise ("Attack on Titan" joins "Attack on Titan Collection")."""
    try:
        series, _ = core.library.list_series()
    except Exception:  # noqa: BLE001 - collections still list their films
        return {}
    out: dict[str, list[Series]] = {}
    for name in names:
        f = _franchise(name)
        hits = [s for s in series if name in s.collections or (f and known.norm(s.title) == f)]
        if hits:
            out[name] = hits
    return out


def show_row(s: Series) -> dict[str, Any]:
    return {"key": s.key, "title": s.title, "year": s.year, "seasons": s.season_count,
            "episodes": s.episode_count, "art": art_ref(s)}


def _health_by_file(core: Core) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for f in (core.last_check() or {}).get("findings", []):
        if f.get("file"):
            out.setdefault(f["file"], []).append(f)
    return out


CODEC = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "vp9": "VP9", "mpeg2video": "MPEG-2"}


def video_facts(info: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolution, HDR and codec from a cached probe ({"res": "4K", "hdr": "HDR10", ...})."""
    v = (info or {}).get("video") or {}
    if not v:
        return None
    w, h = v.get("width") or 0, v.get("height") or 0
    res = ("4K" if w >= 3200 or h >= 1600 else "1080p" if w >= 1800 or h >= 1000
           else "720p" if w >= 1200 or h >= 700 else "SD")
    hdr = v.get("hdr") if v.get("hdr") and v.get("hdr") != "SDR" else None
    codec = v.get("codec") or ""
    return {"res": res, "hdr": hdr, "codec": CODEC.get(codec, codec.upper() or None)}


def _probes(core: Core, ms: list[Movie]) -> dict[str, dict[str, Any]]:
    cached = getattr(core.prober, "cached", None)  # tests swap in a plain function
    return cached([m.file for m in ms if m.file]) if cached else {}


def _version_key(m: Movie) -> object:
    return ("tmdb", m.tmdb_id) if m.tmdb_id else ("title", m.title.lower(), m.year)


def _cut_sources(core: Core) -> dict[str, list[str]]:
    """movie key -> titles of the installed cuts that use that exact copy."""
    out: dict[str, list[str]] = {}
    for r in core.db.query("SELECT title, doc FROM timelines"):
        for s in json.loads(r["doc"]).get("sources", {}).values():
            if s.get("kind") != "movie":
                continue
            m = core.library.find_movie(s.get("tmdb"), edition=s.get("edition", ""))
            if m and r["title"] not in out.setdefault(m.key, []):
                out[m.key].append(r["title"])
    return out


def movie_row(m: Movie, health: dict[str, list[dict[str, Any]]],
              probes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    problems = [f for f in health.get(m.file or "", []) if f["severity"] in ("error", "warn")
                and f["check"] != "mismatch"]
    row = {"key": m.key, "title": m.title, "year": m.year, "tmdb_id": m.tmdb_id,
            "source": m.source, "file": m.file, "on_disk": bool(m.file),
            "minutes": round(m.duration / 60) if m.duration else None,
            "edition": _edition(m), "collections": _collections_of(m),
            "problem": problems[0]["message"] if problems else None,
            "problem_severity": problems[0]["severity"] if problems else None,
            "art": art_ref(m)}
    if probes is not None:  # None with a file = never probed: the UI says "Not analysed"
        row["video"] = video_facts(probes.get(m.file or ""))
    return row


def movies(core: Core) -> dict[str, Any]:
    ms, errors = core.library.list_movies()
    health = _health_by_file(core)
    probes = _probes(core, ms)
    used = _cut_sources(core)
    rows = [movie_row(m, health, probes) for m in ms]
    # several copies of one film (Theatrical, Extended): one tile; the first is the
    # copy a cut uses, else the first listed, and the rest hang off it as versions
    groups: dict[object, list[dict[str, Any]]] = {}
    for m, row in zip(ms, rows, strict=True):
        row["source_for"] = used.get(m.key, [])
        groups.setdefault(_version_key(m), []).append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (not r["source_for"], not r["on_disk"]))
        head = group[0]
        head["versions"] = [{"key": r["key"], "edition": r["edition"], "video": r.get("video"),
                             "source_for": r["source_for"]} for r in group]
        for r in group[1:]:
            r["version_of"] = head["key"]
    return {"errors": errors, "movies": rows}


def collections(core: Core) -> dict[str, Any]:
    ms, _ = core.library.list_movies()
    groups: dict[str, list[Movie]] = {}
    for m in ms:
        for c in _collections_of(m):
            groups.setdefault(c, []).append(m)
    cuts = all_cuts(core)["cuts"]
    shows = _show_members(core, list(groups))
    out = []
    for name, members in sorted(groups.items()):
        tmdbs = {m.tmdb_id for m in members if m.tmdb_id}
        keys = {s.key for s in shows.get(name, [])}
        n_cuts = sum(1 for c in cuts if tmdbs & set(c.get("tmdb_ids", []))
                     or c["series_key"] in keys)
        by_year = sorted(members, key=lambda m: m.year or 0)
        out.append({"name": name, "films": len(members), "cuts": n_cuts,
                    "shows": [s.key for s in shows.get(name, [])],
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
    shows = _show_members(core, [name]).get(name, [])
    keys = {s.key for s in shows}
    cuts = [c for c in all_cuts(core)["cuts"]
            if tmdbs & set(c.get("tmdb_ids", [])) or c["series_key"] in keys]
    probes = _probes(core, members)
    return {"name": name, "films": [movie_row(m, health, probes) for m in members],
            "shows": [show_row(s) for s in shows],
            "minutes": round(total / 60), "suggestions": sugg, "cuts": cuts}


def movie(core: Core, key: str) -> dict[str, Any]:
    ms, _ = core.library.list_movies()
    m = next((x for x in ms if x.key == key), None) or core.library.get_movie(key)
    health = _health_by_file(core)
    row = movie_row(m, health, _probes(core, [m]))
    info: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = []
    if m.file and Path(m.file).is_file():
        findings, edition, info = core.checker.check_file(m.file, m.title, m.year, m.tmdb_id)
        row["edition"] = edition or row["edition"]
    siblings = []
    near = [x for x in ms if set(_collections_of(x)) & set(row["collections"])]
    probes = _probes(core, near)
    show_sets = _show_members(core, row["collections"])
    for c in row["collections"]:
        siblings.append({"name": c, "shows": [show_row(s) for s in show_sets.get(c, [])],
                         "films": [movie_row(x, health, probes) for x in sorted(
            (x for x in ms if c in _collections_of(x)), key=lambda x: (x.year or 0, x.title))]})
    cuts = [c for c in all_cuts(core)["cuts"] if m.tmdb_id and m.tmdb_id in c["tmdb_ids"]]
    if info is not None:
        row["video"] = video_facts(info)
    versions = [{"key": x.key, "edition": _edition(x), "on_disk": bool(x.file)}
                for x in ms if x.key != m.key and _version_key(x) == _version_key(m)]
    used = _cut_sources(core)
    return {**row, "probe": info, "findings": findings + health.get(m.file or "", []),
            "collection_films": siblings, "cuts": cuts, "other_versions": versions,
            "source_for": used.get(m.key, [])}


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

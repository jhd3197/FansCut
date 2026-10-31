"""AniFiller-format snapshots: load data/fillers/*.json, refresh from a URL.

Format per the AniFiller README: a show is {title, mal_id, anilist_id,
episodes: [{episode, title, type, aired_date}]} with type one of manga-canon,
anime-canon, mixed-manga, filler. A file may hold one show, a list of shows,
or {"shows": [...]}. No dataset ships with FansCut; refresh() fetches it.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("fanscut.fillers")

TYPE_TO_LABEL = {
    "manga-canon": "canon",
    "anime-canon": "canon",
    "mixed-manga": "mixed",
    "filler": "filler",
}
CACHE_NAME = "anifiller.min.json"


@dataclass
class FillerShow:
    title: str
    mal_id: int | None
    anilist_id: int | None
    labels: dict[int, str] = field(default_factory=dict)  # episode number -> label


def _shows_in(doc: Any) -> list[dict[str, Any]]:
    if isinstance(doc, list):
        return [d for d in doc if isinstance(d, dict)]
    if isinstance(doc, dict):
        if isinstance(doc.get("shows"), list):
            return _shows_in(doc["shows"])
        if "episodes" in doc:
            return [doc]
        return [d for d in doc.values() if isinstance(d, dict) and "episodes" in d]
    return []


def parse_show(raw: dict[str, Any]) -> FillerShow:
    show = FillerShow(str(raw.get("title") or ""), raw.get("mal_id"), raw.get("anilist_id"))
    for ep in raw.get("episodes") or []:
        label = TYPE_TO_LABEL.get(str(ep.get("type")))
        num = ep.get("episode")
        if label and isinstance(num, int):
            show.labels[num] = label
    return show


class FillerIndex:
    def __init__(self) -> None:
        self.by_mal: dict[int, FillerShow] = {}
        self.by_anilist: dict[int, FillerShow] = {}
        self.files = 0
        self.shows = 0

    def add(self, show: FillerShow) -> None:
        self.shows += 1
        if isinstance(show.mal_id, int):
            self.by_mal[show.mal_id] = show
        if isinstance(show.anilist_id, int):
            self.by_anilist[show.anilist_id] = show

    def find(self, mal_id: int | None, anilist_id: int | None) -> FillerShow | None:
        return (self.by_mal.get(mal_id) if mal_id else None) or (
            self.by_anilist.get(anilist_id) if anilist_id else None
        )


def load_dir(directory: Path) -> FillerIndex:
    """Every *.json in the directory; a bad file is logged and skipped."""
    index = FillerIndex()
    if not directory.is_dir():
        return index
    for f in sorted(directory.glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("skipping filler file %s: %s", f.name, exc)
            continue
        index.files += 1
        for raw in _shows_in(doc):
            index.add(parse_show(raw))
    return index


def refresh(directory: Path, url: str, session: requests.Session | None = None) -> dict[str, Any]:
    """Download the snapshot into the directory. Never raises: returns {ok, message}."""
    try:
        resp = (session or requests).get(url, timeout=60)
        resp.raise_for_status()
        doc = resp.json()
        shows = _shows_in(doc)
        if not shows:
            return {"ok": False, "message": "downloaded file has no shows"}
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / (CACHE_NAME + ".part")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, directory / CACHE_NAME)  # keep the old copy if the write fails
    except (requests.RequestException, ValueError, OSError) as exc:
        return {"ok": False, "message": f"refresh failed: {exc.__class__.__name__}: {exc}"}
    return {"ok": True, "message": f"saved {len(shows)} shows to {CACHE_NAME}", "shows": len(shows)}

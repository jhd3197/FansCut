"""Pick the catalogue entry for a library series: search both catalogues, score titles."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from ..errors import ClientError
from ..models import Series
from . import anilist, jikan
from .http import CachedHttp

AUTO_THRESHOLD = 0.85


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def score(series: Series, cand: dict) -> float:
    wanted = [norm(t) for t in (series.title, *series.alt_titles) if t]
    names = [norm(n) for n in (cand.get("names") or [cand.get("title", "")]) if n]
    best = max((SequenceMatcher(None, w, n).ratio() for w in wanted for n in names), default=0.0)
    if series.year and cand.get("year"):
        best -= 0.1 * min(abs(series.year - cand["year"]), 3) / 3
    return round(max(best, 0.0), 3)


def candidates(http: CachedHttp, series: Series, query: str | None = None) -> list[dict]:
    """Scored candidates, best first, AniList and Jikan merged on shared MAL id."""
    q = query or series.title
    merged: dict[int | str, dict] = {}
    for fn in (anilist.search, jikan.search):
        try:
            found = fn(http, q)
        except ClientError:
            continue  # offline or rate-limited: the other catalogue may still answer
        for c in found:
            key = c.get("mal_id") or f"{c['source']}:{c.get('anilist_id')}"
            if key in merged:
                cur = merged[key]
                cur["anilist_id"] = cur.get("anilist_id") or c.get("anilist_id")
                cur["names"] = list(dict.fromkeys([*cur["names"], *c["names"]]))
                cur["source"] = "anilist+jikan"
            else:
                merged[key] = dict(c)
    out = list(merged.values())
    for c in out:
        c["score"] = score(series, c)
    return sorted(out, key=lambda c: c["score"], reverse=True)


def auto_match(http: CachedHttp, series: Series) -> dict | None:
    best = candidates(http, series)
    return best[0] if best and best[0]["score"] >= AUTO_THRESHOLD else None

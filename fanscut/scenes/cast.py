"""The cast of a film: actor, character and photo, in billing order.

Plex already knows the cast of every film it has matched (its ``Role`` tags),
so films in Plex need nothing else. Other films use TMDB credits when a TMDB
API key is set.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import requests

from ..errors import FansCutError

TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/w185"
MAX_CAST = 60


def from_plex(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Plex metadata (one item of /library/metadata/<id>) -> cast entries."""
    out = []
    for i, r in enumerate(meta.get("Role") or []):
        name = r.get("tag")
        if not name:
            continue
        out.append(
            {
                "name": name,
                "character": r.get("role") or "",
                "photo": r.get("thumb") or "",
                "order": i,
            }
        )
    return out[:MAX_CAST]


def from_tmdb(credits: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in sorted(credits.get("cast") or [], key=lambda c: c.get("order", 999)):
        if not c.get("name"):
            continue
        path = c.get("profile_path")
        out.append(
            {
                "name": c["name"],
                "character": c.get("character") or "",
                "photo": f"{TMDB_IMG}{path}" if path else "",
                "order": c.get("order", len(out)),
                "tmdb_person": c.get("id"),
            }
        )
    return out[:MAX_CAST]


def fetch_plex(plex: Any, rating_key: str) -> list[dict[str, Any]]:
    meta = plex._get(f"/library/metadata/{rating_key}")["MediaContainer"].get("Metadata") or [{}]
    return from_plex(meta[0])


def fetch_tmdb(
    api_key: str, tmdb_id: int, session: requests.Session | None = None
) -> list[dict[str, Any]]:
    http = session or requests
    # v4 read tokens are long JWTs (Bearer); v3 keys are 32 hex characters (query).
    bearer = len(api_key) > 40
    try:
        r = http.get(
            f"{TMDB}/movie/{int(tmdb_id)}/credits",
            params=None if bearer else {"api_key": api_key},
            headers={"Authorization": f"Bearer {api_key}"} if bearer else None,
            timeout=20,
        )
        r.raise_for_status()
        return from_tmdb(r.json())
    except requests.RequestException as exc:
        raise FansCutError(f"TMDB credits failed: {exc.__class__.__name__}") from exc


def fetch_keywords(
    api_key: str, tmdb_id: int, session: requests.Session | None = None
) -> list[str]:
    """The film's TMDB keywords ("christmas", "boarding school"), lower case."""
    http = session or requests
    bearer = len(api_key) > 40
    try:
        r = http.get(
            f"{TMDB}/movie/{int(tmdb_id)}/keywords",
            params=None if bearer else {"api_key": api_key},
            headers={"Authorization": f"Bearer {api_key}"} if bearer else None,
            timeout=20,
        )
        r.raise_for_status()
    except requests.RequestException as exc:
        raise FansCutError(f"TMDB keywords failed: {exc.__class__.__name__}") from exc
    return [str(k.get("name", "")).lower() for k in r.json().get("keywords") or []]


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]+", " ", s).strip()


def match_person(cast: list[dict[str, Any]], said: str) -> dict[str, Any] | None:
    """The cast entry a free-text name refers to ("Harry", "Dumbledore", "Daniel Radcliffe")."""
    want = _fold(said)
    if not want:
        return None
    words = set(want.split())
    best, score = None, 0
    for c in cast:
        for field in ("character", "name"):
            have = _fold(c.get(field) or "")
            if not have:
                continue
            if have == want:
                return c
            # "harry" matches "harry potter"; "professor dumbledore" matches "albus dumbledore"
            common = len(words & set(have.split()))
            if common > score:
                best, score = c, common
    return best if score else None


def resolve_people(
    cast: list[dict[str, Any]], people: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Fill in actor names and canonical character names from the cast where possible."""
    out = []
    for p in people:
        hit = match_person(cast, p.get("character") or p.get("name") or "")
        if hit:
            out.append(
                {"name": hit["name"], "character": hit.get("character") or p.get("character", "")}
            )
        else:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = (p.get("name"), p.get("character"))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq

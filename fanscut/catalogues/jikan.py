"""Jikan (MyAnimeList) search and episode flags. Rate limit: ~3 requests/second."""

from __future__ import annotations

from typing import Any

from .http import CachedHttp

BASE = "https://api.jikan.moe/v4"
MAX_PAGES = 40  # 100 episodes a page; a guard against a runaway pager
EPISODES_TTL = 3 * 86400  # filler flags change while a show airs; searches keep cache_days


def _slim_episodes(data: Any) -> Any:
    """Keep only what episode_flags reads; Jikan pages carry titles, scores, urls..."""
    if not isinstance(data, dict):
        return data
    return {"pagination": {"has_next_page": bool((data.get("pagination") or {})
                                                  .get("has_next_page"))},
            "data": [{"mal_id": e.get("mal_id"), "filler": e.get("filler"),
                      "recap": e.get("recap")} for e in data.get("data", [])
                     if isinstance(e, dict)]}


def _aired_year(m: dict[str, Any]) -> int | None:
    start = (((m.get("aired") or {}).get("prop") or {}).get("from")) or {}
    return start.get("year")


def search(http: CachedHttp, query: str) -> list[dict[str, Any]]:
    data = http.request("GET", f"{BASE}/anime", params={"q": query, "limit": 8, "sfw": "true"})
    out = []
    for m in data.get("data", []):
        names = [n for n in (m.get("title"), m.get("title_english"), m.get("title_japanese"),
                             *(t.get("title") for t in m.get("titles", []))) if n]
        out.append({
            "source": "jikan", "mal_id": m["mal_id"], "anilist_id": None,
            "title": m.get("title_english") or m.get("title") or "", "names": names,
            "year": m.get("year") or _aired_year(m),
            "episodes": m.get("episodes"), "format": m.get("type"),
        })
    return out


def episode_flags(http: CachedHttp, mal_id: int) -> dict[int, dict[str, bool]]:
    """{episode: {"filler": bool, "recap": bool}} across every page."""
    flags: dict[int, dict[str, bool]] = {}
    for page in range(1, MAX_PAGES + 1):
        data = http.request("GET", f"{BASE}/anime/{mal_id}/episodes", params={"page": page},
                            ttl=EPISODES_TTL, transform=_slim_episodes)
        for e in data.get("data", []):
            n = e.get("mal_id")
            if isinstance(n, int):
                flags[n] = {"filler": bool(e.get("filler")), "recap": bool(e.get("recap"))}
        if not (data.get("pagination") or {}).get("has_next_page"):
            break
    return flags

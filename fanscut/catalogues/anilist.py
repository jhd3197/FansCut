"""AniList GraphQL search (keyless)."""

from __future__ import annotations

from typing import Any

from .http import CachedHttp

URL = "https://graphql.anilist.co"
QUERY = """
query ($q: String) {
  Page(perPage: 8) {
    media(search: $q, type: ANIME) {
      id idMal format episodes seasonYear
      title { romaji english native }
      synonyms
    }
  }
}"""


def search(http: CachedHttp, query: str) -> list[dict[str, Any]]:
    data = http.request("POST", URL, body={"query": QUERY, "variables": {"q": query}})
    out = []
    for m in (data.get("data") or {}).get("Page", {}).get("media", []) or []:
        t = m.get("title") or {}
        names = [n for n in (t.get("romaji"), t.get("english"), t.get("native"),
                             *(m.get("synonyms") or [])) if n]
        out.append({
            "source": "anilist", "anilist_id": m["id"], "mal_id": m.get("idMal"),
            "title": t.get("english") or t.get("romaji") or "", "names": names,
            "year": m.get("seasonYear"), "episodes": m.get("episodes"), "format": m.get("format"),
        })
    return out

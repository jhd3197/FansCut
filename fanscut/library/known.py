"""Facts about well-known releases, used by the library check.

Runtimes are minutes (end credits included). `lang` is the original audio
language; `forced_subs` means the film has foreign-language dialogue that
needs forced subtitles in the viewer's language. Only metadata: no media.
"""

from __future__ import annotations

from typing import Any

KNOWN_MOVIES: list[dict[str, Any]] = [
    {"tmdb": 49051, "title": "The Hobbit: An Unexpected Journey", "year": 2012,
     "set": "The Hobbit", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 169, "Extended": 182}},
    {"tmdb": 57158, "title": "The Hobbit: The Desolation of Smaug", "year": 2013,
     "set": "The Hobbit", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 161, "Extended": 186}},
    {"tmdb": 122917, "title": "The Hobbit: The Battle of the Five Armies", "year": 2014,
     "set": "The Hobbit", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 144, "Extended": 164}},
    {"tmdb": 120, "title": "The Lord of the Rings: The Fellowship of the Ring", "year": 2001,
     "set": "The Lord of the Rings", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 178, "Extended": 228}},
    {"tmdb": 121, "title": "The Lord of the Rings: The Two Towers", "year": 2002,
     "set": "The Lord of the Rings", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 179, "Extended": 235}},
    {"tmdb": 122, "title": "The Lord of the Rings: The Return of the King", "year": 2003,
     "set": "The Lord of the Rings", "lang": "eng", "forced_subs": True,
     "editions": {"Theatrical": 201, "Extended": 263}},
]

# seasons: TVDB aired order, episodes per season. extras: named pieces fans expect.
KNOWN_SERIES: list[dict[str, Any]] = [
    {"tvdb": 267440, "tmdb": 1429, "title": "Attack on Titan", "lang": "jpn",
     "seasons": {1: 25, 2: 12, 3: 22, 4: 30},
     "extras": [{"name": "The Final Chapters", "episodes": ["S04E29", "S04E30"]},
                {"name": "OVAs", "season": 0}]},
]


def norm(title: str) -> str:
    """Comparable title: lowercase, ':' and ' - ' treated alike, punctuation dropped."""
    t = title.lower().replace(":", " ").replace(" - ", " ")
    return " ".join("".join(c for c in t if c.isalnum() or c.isspace()).split())


def movie(tmdb: int | None, title: str = "", year: int | None = None) -> dict[str, Any] | None:
    for k in KNOWN_MOVIES:
        if tmdb and k["tmdb"] == tmdb:
            return k
    n = norm(title)
    for k in KNOWN_MOVIES:
        if n and norm(k["title"]) == n and (not year or year == k["year"]):
            return k
    return None


def series(tvdb: int | None, title: str = "") -> dict[str, Any] | None:
    for k in KNOWN_SERIES:
        if tvdb and k["tvdb"] == tvdb:
            return k
    n = norm(title)
    return next((k for k in KNOWN_SERIES if n and norm(k["title"]) == n), None)

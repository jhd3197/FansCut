"""Per-episode labels: user override > AniFiller snapshot > Jikan flags > unknown.

Numbering: AniFiller and Jikan count episodes absolutely. A season-specific match
(one catalogue entry per season) counts within the season instead; the caller
passes the right numbers in.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Episode


@dataclass
class LabelResult:
    label: str
    source: str  # override | anifiller | jikan | none


def catalogue_numbers(episodes: list[Episode]) -> dict[str, int | None]:
    """episode id -> absolute number. Sonarr's absoluteEpisodeNumber when present,
    else a running count over regular seasons (season 0 / specials get None)."""
    out: dict[str, int | None] = {}
    running = 0
    for e in sorted(episodes, key=lambda e: (e.season, e.number)):
        if e.season == 0:
            out[e.id] = None
            continue
        running += 1
        out[e.id] = e.absolute if e.absolute else running
    return out


def resolve(
    episodes: list[Episode],
    numbers: dict[str, int | None],
    anifiller: dict[int, str] | None,
    jikan: dict[int, dict[str, bool]] | None,
    overrides: dict[str, str],
) -> dict[str, LabelResult]:
    """Label each episode; `numbers` maps episode id -> catalogue episode number."""
    out: dict[str, LabelResult] = {}
    for e in episodes:
        n = numbers.get(e.id)
        if e.id in overrides:
            out[e.id] = LabelResult(overrides[e.id], "override")
        elif n is not None and anifiller and n in anifiller:
            out[e.id] = LabelResult(anifiller[n], "anifiller")
        elif n is not None and jikan and n in jikan:
            f = jikan[n]
            # Jikan has only filler/recap flags; recaps are not story, so they count as filler.
            out[e.id] = LabelResult("filler" if f["filler"] or f["recap"] else "canon", "jikan")
        else:
            out[e.id] = LabelResult("unknown", "none")
    return out

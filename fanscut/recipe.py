"""Cut recipes: a small JSON file with the choices for a series, no video data.

    {"schema": 1, "series": {"title", "mal_id", "anilist_id"}, "preset": "...",
     "options": {...},
     "episodes": {"S01E03": {"label", "include", "trims": {"intro": [s, e] | null}}}}

Episodes are matched on import by season+episode number.
"""

from __future__ import annotations

from typing import Any

from .errors import FansCutError
from .models import LABELS, TRIM_KINDS
from .planner import PRESETS

SCHEMA = 1
OPTION_KEYS = ("unknown_policy", "keep_intro_first_episode", "pad", "merge_gap", "min_segment",
               "transition")


class RecipeError(FansCutError):
    pass


def build(title: str, mal_id: int | None, anilist_id: int | None, settings: dict[str, Any],
          overrides: dict[str, dict[str, Any]],
          trims: dict[str, dict[str, tuple[float, float] | None]]) -> dict[str, Any]:
    episodes: dict[str, dict[str, Any]] = {}
    for eid, o in overrides.items():
        episodes.setdefault(eid, {}).update({k: v for k, v in o.items() if v is not None})
    for eid, t in trims.items():
        episodes.setdefault(eid, {})["trims"] = {k: list(v) if v else None for k, v in t.items()}
    return {
        "schema": SCHEMA,
        "series": {"title": title, "mal_id": mal_id, "anilist_id": anilist_id},
        "preset": settings.get("preset"),
        "options": {k: settings[k] for k in OPTION_KEYS if k in settings},
        "episodes": dict(sorted(episodes.items())),
    }


def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or v < 0:
        raise RecipeError(f"{what}: expected a non-negative number")
    return float(v)


def validate(doc: Any) -> dict[str, Any]:
    """Return a cleaned copy or raise RecipeError."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise RecipeError(f"not a schema {SCHEMA} recipe")
    series = doc.get("series", {})
    if not isinstance(series, dict):
        raise RecipeError("series must be an object")
    preset = doc.get("preset")
    if preset is not None and preset not in PRESETS:
        raise RecipeError(f"unknown preset {preset!r}")
    options = doc.get("options", {})
    if not isinstance(options, dict):
        raise RecipeError("options must be an object")
    if bad := sorted(set(options) - set(OPTION_KEYS)):
        raise RecipeError(f"unknown option(s): {', '.join(bad)}")
    episodes: dict[str, dict[str, Any]] = {}
    for eid, e in (doc.get("episodes") or {}).items():
        if not isinstance(e, dict):
            raise RecipeError(f"{eid}: expected an object")
        clean: dict[str, Any] = {}
        if "label" in e:
            if e["label"] not in LABELS:
                raise RecipeError(f"{eid}: bad label {e['label']!r}")
            clean["label"] = e["label"]
        if "include" in e:
            if not isinstance(e["include"], bool):
                raise RecipeError(f"{eid}: include must be true or false")
            clean["include"] = e["include"]
        if "trims" in e:
            trims: dict[str, list[float] | None] = {}
            for kind, rng in (e["trims"] or {}).items():
                if kind not in TRIM_KINDS:
                    raise RecipeError(f"{eid}: unknown trim {kind!r}")
                if rng is None:
                    trims[kind] = None
                    continue
                if not isinstance(rng, list) or len(rng) != 2:
                    raise RecipeError(f"{eid}.{kind}: expected [start, end]")
                s, t = _num(rng[0], f"{eid}.{kind}"), _num(rng[1], f"{eid}.{kind}")
                if t <= s:
                    raise RecipeError(f"{eid}.{kind}: end must be after start")
                trims[kind] = [s, t]
            clean["trims"] = trims
        episodes[str(eid)] = clean
    return {"series": series, "preset": preset, "options": options, "episodes": episodes}

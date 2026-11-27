"""The shared tag list (``tags_v1.json``, the same list Marcarr ships; see docs/markers.md).

Content tags have plain ids (``christmas``); FansCut's own cut tags carry
``fc:`` (``fc:filler``). The app shows names only, never the prefix.
``signals`` are what Marcarr measures (faces, shots, brightness, sound): they
are not tags and are never shown as tags.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

SMART = "fc:"


@cache
def load() -> dict[str, Any]:
    raw = resources.files("fanscut.scenes").joinpath("tags_v1.json").read_text(encoding="utf-8")
    return json.loads(raw)


def families() -> list[str]:
    return list(load()["families"])


def content_tags(families_off: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    off = set(families_off)
    return [t for t in load()["tags"] if t["family"] not in off and not t.get("retired")]


def cut_tags(detect: str | None = None) -> list[dict[str, Any]]:
    return [t for t in load()["cut_tags"] if detect is None or t["detect"] == detect]


@cache
def _by_id() -> dict[str, dict[str, Any]]:
    data = load()
    return {t["id"]: t for t in [*data["tags"], *data["cut_tags"]]}


def get(tag: str) -> dict[str, Any] | None:
    return _by_id().get(tag)


def is_smart(tag: str) -> bool:
    return tag.startswith(SMART)


def name(tag: str) -> str:
    """Display name: the list's name, else the id without ``fc:`` and dashes."""
    t = get(tag)
    if t:
        return str(t["name"])
    plain = tag.removeprefix(SMART).replace("-", " ")
    return plain[:1].upper() + plain[1:]


def public() -> dict[str, Any]:
    """What ``GET /api/scenes/vocab`` returns."""
    data = load()
    return {
        "version": data["version"],
        "families": data["families"],
        "tags": data["tags"],
        "cut_tags": data["cut_tags"],
        "signals": data.get("signals", []),
    }

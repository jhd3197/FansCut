"""The shared tag list, read from Marcarr (``GET /tags/vocabulary``; see docs/markers.md).

Marcarr owns the list: FansCut keeps no copy in its code. The running scene
service registers how to fetch it (``use``); the last list fetched is kept in a
file next to the database, so FansCut still names its tags when Marcarr is down
or not set up yet. With neither, the list is empty and tags show by their ids.

Content tags have plain ids (``christmas``); FansCut's own cut tags carry
``fc:`` (``fc:filler``). The app shows names only, never the prefix.
``signals`` are what Marcarr measures (faces, shots, brightness, sound): they
are not tags and are never shown as tags. ``mine`` are the custom tags made in
Marcarr ("My tags").
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

SMART = "fc:"
FRESH = 300.0  # seconds a fetched list is reused
RETRY = 30.0  # seconds before asking a Marcarr that did not answer again
EMPTY: dict[str, Any] = {"version": 0, "families": [], "tags": [], "cut_tags": [], "signals": [],
                         "mine": []}
log = logging.getLogger(__name__)

_lock = threading.Lock()
_source: Callable[[], dict[str, Any]] | None = None
_file: Callable[[], Path] | None = None
_data: dict[str, Any] | None = None
_next = 0.0  # when to ask Marcarr again
_ids: tuple[int, dict[str, dict[str, Any]]] | None = None


def use(
    source: Callable[[], dict[str, Any]] | None, cache_file: Callable[[], Path] | None = None
) -> None:
    """Where the list comes from (a call to Marcarr) and the file that keeps the last
    one (asked for when needed: the data folder is a setting)."""
    global _source, _file
    with _lock:
        _source, _file = source, cache_file
    forget()


def forget() -> None:
    """Fetch the list again on the next read (Marcarr's address changed, a tag was added)."""
    global _data, _next, _ids
    with _lock:
        _data, _next, _ids = None, 0.0, None


def _valid(data: Any) -> bool:
    lists = ("families", "tags", "cut_tags")
    return isinstance(data, dict) and all(isinstance(data.get(k), list) for k in lists)


def _path() -> Path | None:
    try:
        return _file() if _file is not None else None
    except Exception:  # noqa: BLE001 - no settings yet: no file
        return None


def _saved() -> dict[str, Any] | None:
    path = _path()
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if _valid(data) else None


def load() -> dict[str, Any]:
    global _data, _next
    with _lock:
        now = time.monotonic()
        if _data is not None and now < _next:
            return _data
        fetched = None
        if _source is not None:
            try:
                fetched = _source()
            except Exception as exc:  # noqa: BLE001 - Marcarr down or not set up: use the last list
                log.debug("the tag list could not be read from Marcarr: %s", exc)
        if _valid(fetched):
            assert fetched is not None
            path = _path()
            if path is not None and fetched != _data:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(fetched), encoding="utf-8")
                except OSError:
                    pass
            _data, _next = fetched, now + FRESH
        else:
            _data, _next = _data or _saved() or EMPTY, now + RETRY
        return _data


def families() -> list[str]:
    return list(load()["families"])


def content_tags(families_off: list[str] | tuple[str, ...] = ()) -> list[dict[str, Any]]:
    off = set(families_off)
    return [t for t in load()["tags"] if t["family"] not in off and not t.get("retired")]


def cut_tags(detect: str | None = None) -> list[dict[str, Any]]:
    return [t for t in load()["cut_tags"] if detect is None or t["detect"] == detect]


def custom_tags() -> list[dict[str, Any]]:
    """The custom tags made in Marcarr, retired ones left out."""
    return [t for t in load().get("mine") or [] if not t.get("retired")]


def _by_id() -> dict[str, dict[str, Any]]:
    global _ids
    data = load()
    if _ids is None or _ids[0] != id(data):
        # a bundled tag wins over a custom one of the same id
        rows = [*(data.get("mine") or []), *data["tags"], *data["cut_tags"]]
        _ids = (id(data), {t["id"]: t for t in rows})
    return _ids[1]


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
        "mine": custom_tags(),
    }

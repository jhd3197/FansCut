"""Small text helpers for scene data: matching a said name to the cast, and telling
a spoken subtitle line from a sound caption."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# "[door slams]", "(laughs)", "♪ theme ♪": not something a person says
_NOT_SPEECH = re.compile(r"^[\s\-]*([\[(].*[\])]|[♪♫#*].*)[\s\-]*$")


def speech(text: str) -> bool:
    return not _NOT_SPEECH.match(text.strip())


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

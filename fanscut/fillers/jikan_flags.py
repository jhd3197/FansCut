"""Jikan episode flags as a filler fallback; failures mean "no data"."""

from __future__ import annotations

from ..catalogues import jikan
from ..catalogues.http import CachedHttp
from ..errors import ClientError


def fetch(http: CachedHttp, mal_id: int) -> dict[int, dict[str, bool]] | None:
    try:
        flags = jikan.episode_flags(http, mal_id)
    except ClientError:
        return None
    # No episode flagged at all usually means nobody filled the flags in, not a
    # show with zero filler, so "canon" would be a guess presented as data.
    if not any(f["filler"] or f["recap"] for f in flags.values()):
        return None
    return flags

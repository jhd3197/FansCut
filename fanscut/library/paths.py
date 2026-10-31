"""Remote->local path prefix mapping (the container sees different paths than Sonarr/Plex)."""

from __future__ import annotations

from collections.abc import Iterable

from ..config import PathMapping


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def map_path(path: str, mappings: Iterable[PathMapping]) -> str:
    """Swap the longest matching remote prefix for its local one; no match = unchanged."""
    norm = _norm(path)
    best: PathMapping | None = None
    for m in mappings:
        remote = _norm(m.remote).rstrip("/")
        if (norm == remote or norm.startswith(remote + "/")) and (
            best is None or len(remote) > len(_norm(best.remote).rstrip("/"))
        ):
            best = m
    if best is None:
        return path
    rest = norm[len(_norm(best.remote).rstrip("/")) :]
    return best.local.rstrip("/\\") + rest

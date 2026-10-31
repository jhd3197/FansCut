"""Find stretches of audio two clips share, as diagonals of a frame-similarity matrix."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .audio import FRAME_RATE

GAP_FRAMES = 3  # dips shorter than this inside a match do not split it


@dataclass
class Match:
    a_start: float  # seconds into clip A
    a_end: float
    b_start: float
    b_end: float
    score: float  # mean frame similarity over the match

    @property
    def length(self) -> float:
        return self.a_end - self.a_start


def _runs(mask: np.ndarray, gap: int) -> list[tuple[int, int]]:
    """[start, end) of True runs, bridging gaps of up to `gap` False values."""
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    runs: list[list[int]] = []
    for s, e in zip(edges[::2], edges[1::2], strict=True):
        if runs and s - runs[-1][1] <= gap:
            runs[-1][1] = int(e)
        else:
            runs.append([int(s), int(e)])
    return [(s, e) for s, e in runs]


def find_shared(
    a: np.ndarray, b: np.ndarray, threshold: float, min_seconds: float, max_seconds: float
) -> list[Match]:
    """Shared stretches between feature clips, longest first.

    Runs longer than max_seconds are dropped (a whole shared scene is not an opening).
    """
    if len(a) == 0 or len(b) == 0:
        return []
    sim = a @ b.T
    hit = sim > threshold
    min_frames = int(min_seconds * FRAME_RATE)
    found: list[Match] = []
    for d in range(-(len(a) - 1), len(b)):  # d = j - i
        diag_hit = np.diagonal(hit, offset=d)
        if diag_hit.sum() < min_frames:
            continue
        i0 = max(0, -d)
        for s, e in _runs(diag_hit, GAP_FRAMES):
            length = e - s
            if length < min_frames or length / FRAME_RATE > max_seconds:
                continue
            vals = np.diagonal(sim, offset=d)[s:e]
            i, j = i0 + s, i0 + s + d
            found.append(Match(i / FRAME_RATE, (i + length) / FRAME_RATE,
                               j / FRAME_RATE, (j + length) / FRAME_RATE, float(vals.mean())))
    found.sort(key=lambda m: m.length, reverse=True)
    return found

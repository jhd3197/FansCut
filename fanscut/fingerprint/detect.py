"""OP/ED/recap/preview detection across a season.

Core (`detect`) is pure: feature clips in, time ranges out. `analyze_files`
decodes real episodes and calls it.

  intro/outro  the stretch every episode shares near its start / end
  recap        the head of episode N that repeats the tail of episode N-1
  preview      the tail of episode N that repeats the head of episode N+1
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

import numpy as np

from ..config import FFmpegCfg, FingerprintCfg
from ..render import ffmpeg
from .audio import FRAME_RATE, features
from .match import Match, find_shared

PARTNERS = 3  # other episodes each one is compared against
CLUSTER_TOL = 3.0  # seconds: matches this close count as the same stretch
RECAP_MIN = 8.0  # recaps and previews can be shorter than a theme song
MERGE_BREAK = 10.0  # recap pieces further apart than this stay separate
SNAP = 3.0  # a preview ending this near the end of the episode runs to it

Range = tuple[float, float]


@dataclass
class EpisodeClips:
    head: np.ndarray  # features of the first N minutes
    tail: np.ndarray  # features of the last N minutes
    tail_start: float  # seconds into the episode where `tail` begins
    duration: float


def _consensus(cands: list[Range]) -> Range | None:
    """The range most candidates agree on (median of the biggest cluster)."""
    best: list[Range] = []
    for c in cands:
        group = [o for o in cands if abs(o[0] - c[0]) <= CLUSTER_TOL
                 and abs(o[1] - c[1]) <= CLUSTER_TOL]
        if len(group) > len(best):
            best = group
    if not best:
        return None
    return (round(statistics.median(g[0] for g in best), 2),
            round(statistics.median(g[1] for g in best), 2))


def _pairs(n: int) -> list[tuple[int, int]]:
    seen: set[tuple[int, int]] = set()
    for i in range(n):
        for k in range(1, min(PARTNERS, n - 1) + 1):
            j = (i + k) % n
            seen.add((min(i, j), max(i, j)))
    return sorted(seen)


def _theme(clips: list[EpisodeClips], which: str, fp: FingerprintCfg) -> list[Range | None]:
    cands: list[list[Range]] = [[] for _ in clips]
    for i, j in _pairs(len(clips)):
        a, b = (getattr(clips[i], which), getattr(clips[j], which))
        shift_a = clips[i].tail_start if which == "tail" else 0.0
        shift_b = clips[j].tail_start if which == "tail" else 0.0
        matches = find_shared(a, b, fp.threshold, fp.min_segment_seconds, fp.max_segment_seconds)
        for m in matches[:3]:
            cands[i].append((m.a_start + shift_a, m.a_end + shift_a))
            cands[j].append((m.b_start + shift_b, m.b_end + shift_b))
    return [_consensus(c) for c in cands]


def _shifted(shift: float, m: Match) -> Range:
    return (m.a_start + shift, m.a_end + shift)


def _join(matches: list[Match], pick: Callable[[Match], Range]) -> Range | None:
    """Merge nearby pieces into one range; the largest merged group wins."""
    spans = sorted(pick(m) for m in matches)
    groups: list[list[float]] = []
    for s, e in spans:
        if groups and s - groups[-1][1] <= MERGE_BREAK:
            groups[-1][1] = max(groups[-1][1], e)
        else:
            groups.append([s, e])
    if not groups:
        return None
    s, e = max(groups, key=lambda g: g[1] - g[0])
    return (round(s, 2), round(e, 2))


def detect(clips: list[EpisodeClips], fp: FingerprintCfg) -> list[dict[str, Range]]:
    """Per episode (same order as `clips`): any of intro/outro/recap/preview found."""
    out: list[dict[str, Range]] = [{} for _ in clips]
    if len(clips) < 2:
        return out
    for i, r in enumerate(_theme(clips, "head", fp)):
        if r:
            out[i]["intro"] = r
    for i, r in enumerate(_theme(clips, "tail", fp)):
        if r:
            out[i]["outro"] = r
    for i in range(1, len(clips)):  # recap: head of i vs tail of i-1
        ms = find_shared(clips[i].head, clips[i - 1].tail, fp.threshold, RECAP_MIN,
                         fp.max_segment_seconds)
        intro = out[i].get("intro")
        prev_outro, prev_shift = out[i - 1].get("outro"), clips[i - 1].tail_start
        # Only story before the previous ending can be recapped; what follows that
        # ending is a preview of this very episode, not a recap.
        ms = [m for m in ms if (not intro or m.a_start < intro[0] + CLUSTER_TOL)
              and (not prev_outro or m.b_end + prev_shift <= prev_outro[0] + CLUSTER_TOL)]
        if ms and (r := _join(ms, lambda m: (m.a_start, m.a_end))):
            if intro:
                r = (r[0], min(r[1], intro[0]))
            if r[0] <= 1.5:
                r = (0.0, r[1])
            if r[1] - r[0] >= RECAP_MIN * 0.5:
                out[i]["recap"] = r
    for i in range(len(clips) - 1):  # preview: tail of i vs head of i+1
        ms = find_shared(clips[i].tail, clips[i + 1].head, fp.threshold, RECAP_MIN,
                         fp.max_segment_seconds)
        outro = out[i].get("outro")
        shift = clips[i].tail_start
        ms = [m for m in ms if not outro or m.a_start + shift > outro[1] - CLUSTER_TOL]
        if ms and (r := _join(ms, partial(_shifted, shift))):
            if clips[i].duration - r[1] <= SNAP:
                r = (r[0], clips[i].duration)
            if outro:
                r = (max(r[0], outro[1]), r[1])
            if r[1] - r[0] >= RECAP_MIN * 0.5:
                out[i]["preview"] = r
    return out


def analyze_files(
    files: list[tuple[str, str, float]],
    fp: FingerprintCfg,
    ff: FFmpegCfg,
    progress: Callable[[float], None] = lambda _p: None,
) -> dict[str, dict[str, Range]]:
    """files = [(episode_id, path, duration)] in episode order -> {episode_id: ranges}."""
    clips = []
    for n, (_eid, path, duration) in enumerate(files):
        head_len = min(fp.head_minutes * 60, duration)
        tail_len = min(fp.tail_minutes * 60, duration)
        tail_start = max(duration - tail_len, 0.0)
        head = ffmpeg.decode_audio(ff, path, 0, head_len, fp.sample_rate)
        tail = ffmpeg.decode_audio(ff, path, tail_start, tail_len, fp.sample_rate)
        clips.append(EpisodeClips(features(head, fp.sample_rate), features(tail, fp.sample_rate),
                                  tail_start, duration))
        progress(0.7 * (n + 1) / len(files))
    result = detect(clips, fp)
    progress(1.0)
    return {eid: r for (eid, _p, _d), r in zip(files, result, strict=True)}


__all__ = ["FRAME_RATE", "EpisodeClips", "analyze_files", "detect"]

"""Cut planner: a pure function from episodes + labels + overrides + trims to an ordered cut list.

Per kept episode the trim ranges (recap/intro/outro/preview) are removed, the
remaining pieces are padded (so detection error never clips dialogue), clamped,
merged when they are close in the same file, and dropped when too short.
Pieces of different files are never merged here; they just play back to back.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from .models import TRIM_KINDS, Segment

PRESETS = ("canon_only", "canon_mixed", "everything", "custom")
KEEP_LABELS = {
    "canon_only": {"canon"},
    "canon_mixed": {"canon", "mixed"},
    "everything": {"canon", "mixed", "filler", "unknown"},
    "custom": {"canon", "mixed", "filler", "unknown"},
}

Range = tuple[float, float]


@dataclass
class PlanEpisode:
    id: str
    number: int  # the episode's own number, for chapter titles
    season: int
    file: str | None
    duration: float | None
    label: str = "unknown"
    include: bool | None = None  # user override; beats the preset
    trims: dict[str, Range] = field(default_factory=dict)
    title: str = ""


@dataclass
class CutOptions:
    preset: str = "canon_only"
    unknown_policy: Literal["keep", "skip"] = "keep"
    keep_intro_first_episode: bool = True
    pad: float = 0.5
    merge_gap: float = 1.0
    min_segment: float = 5.0
    transition: Literal["none", "fade"] = "none"
    trim_kinds: tuple[str, ...] = TRIM_KINDS  # which kinds to cut out


@dataclass
class CutPlan:
    segments: list[Segment]
    kept: list[str]
    skipped: list[dict[str, str]]  # {episode_id, reason}
    original_runtime: float
    final_runtime: float
    options: CutOptions

    @property
    def time_saved(self) -> float:
        return self.original_runtime - self.final_runtime

    def to_dict(self) -> dict:
        return {
            "segments": [{**asdict(s), "duration": round(s.duration, 3)} for s in self.segments],
            "kept_episodes": self.kept,
            "skipped_episodes": self.skipped,
            "totals": {
                "kept": len(self.kept), "skipped": len(self.skipped),
                "original_runtime": round(self.original_runtime, 3),
                "final_runtime": round(self.final_runtime, 3),
                "time_saved": round(self.time_saved, 3),
            },
            "options": asdict(self.options),
        }


def wants(ep: PlanEpisode, opts: CutOptions) -> tuple[bool, str]:
    """(keep?, reason) from override, preset and unknown policy."""
    if ep.include is not None:
        return ep.include, "user include" if ep.include else "user exclude"
    if opts.preset not in KEEP_LABELS:
        raise ValueError(f"unknown preset {opts.preset!r}")
    if ep.label == "unknown" and opts.preset != "custom":
        keep = opts.unknown_policy == "keep" or opts.preset == "everything"
        return keep, "unknown label kept" if keep else "unknown label skipped"
    keep = ep.label in KEEP_LABELS[opts.preset]
    return keep, f"{ep.label} {'kept' if keep else 'skipped'} by {opts.preset}"


def _subtract(duration: float, cuts: list[Range]) -> list[Range]:
    """[0, duration] minus the cut ranges, as ordered pieces."""
    pieces, pos = [], 0.0
    for s, e in sorted(cuts):
        s, e = max(s, 0.0), min(e, duration)
        if e <= s:
            continue
        if s > pos:
            pieces.append((pos, s))
        pos = max(pos, e)
    if pos < duration:
        pieces.append((pos, duration))
    return pieces


def _merge(pieces: list[Range], gap: float) -> list[Range]:
    out: list[list[float]] = []
    for s, e in sorted(pieces):
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def plan_episode(
    ep: PlanEpisode, opts: CutOptions, keep_intro: bool
) -> list[tuple[Range, str]]:
    """Pieces of one kept episode: ((start, end), reason)."""
    assert ep.duration is not None
    cuts = {k: r for k, r in ep.trims.items() if k in opts.trim_kinds and r}
    if keep_intro:
        cuts.pop("intro", None)
    if not cuts:
        return [((0.0, ep.duration), "full episode")]
    pad = max(opts.pad, 0.0)
    padded = [
        (max(s - pad, 0.0), min(e + pad, ep.duration))
        for s, e in _subtract(ep.duration, list(cuts.values()))
    ]
    pieces = [(s, e) for s, e in _merge(padded, opts.merge_gap) if e - s >= opts.min_segment]
    reason = "trimmed " + ", ".join(sorted(cuts, key=TRIM_KINDS.index))
    return [(p, reason) for p in pieces]


def plan_cut(episodes: list[PlanEpisode], opts: CutOptions) -> CutPlan:
    segments: list[Segment] = []
    kept: list[str] = []
    skipped: list[dict[str, str]] = []
    original = 0.0
    first_kept = True
    for ep in episodes:
        keep, why = wants(ep, opts)
        if ep.duration:
            original += ep.duration
        if not keep:
            skipped.append({"episode_id": ep.id, "reason": why})
            continue
        if not ep.file or not ep.duration:
            skipped.append({"episode_id": ep.id, "reason": "no file on disk"})
            continue
        pieces = plan_episode(ep, opts, first_kept and opts.keep_intro_first_episode)
        if not pieces:
            skipped.append({"episode_id": ep.id, "reason": "nothing left after trimming"})
            continue
        first_kept = False
        kept.append(ep.id)
        segments += [Segment(ep.id, ep.file, round(s, 3), round(e, 3), reason)
                     for (s, e), reason in pieces]
    final = sum(s.duration for s in segments)
    return CutPlan(segments, kept, skipped, original, final, opts)

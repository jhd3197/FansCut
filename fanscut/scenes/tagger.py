"""The AI pass that finds scenes for a tag in one film.

1. **Subtitles.** The film's dialogue, cut into windows of a few minutes at
   scene boundaries, goes to the text model with the tag and its description.
   It answers which windows match and roughly where inside them (cheap: a
   whole film is about 25k tokens).
2. **Look.** Each candidate is checked on a contact sheet (a labelled 3x2 grid
   of frames) by the vision model, which confirms it, tightens the range to
   the tiles that match and names who is on screen.
3. **Optional sweep.** Scenes with almost no dialogue can't be found by step
   1; ``sweep`` shows each of them to the vision model too (one sheet each).

Edges are snapped to camera cuts. Everything comes out as *proposals*; a
person accepts or rejects them in the tagger.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from .. import ai
from ..config import AICfg
from . import index as idx

WINDOW = 300.0  # seconds of film per subtitle window
BATCH_CHARS = 14_000  # subtitle text per request
SHEET = (3, 2)


class WindowHit(BaseModel):
    window: int = Field(description="window number as given")
    start: str = Field(description="where the matching part starts, h:mm:ss or mm:ss of the film")
    end: str = Field(description="where it ends, h:mm:ss or mm:ss of the film")
    why: str = Field(default="", description="a few words on why it matches")
    people: list[str] = Field(default_factory=list, description="characters in it, by name")
    confidence: float = Field(default=0.5, ge=0, le=1)


class WindowHits(BaseModel):
    matches: list[WindowHit] = Field(default_factory=list)


class SheetCheck(BaseModel):
    match: bool
    first_tile: int = Field(default=1, description="first tile number that matches")
    last_tile: int = Field(default=6, description="last tile number that matches")
    summary: str = Field(default="", description="one plain sentence about what happens")
    people: list[str] = Field(default_factory=list, description="characters you can see, by name")
    confidence: float = Field(default=0.5, ge=0, le=1)


TEXT_SYSTEM = """\
You find scenes in a film from its subtitles. You get the tag to look for, what
it means, and numbered windows of dialogue with timestamps (h:mm:ss of the film).
Return every window where the tag clearly applies, with where the matching part
starts and ends inside that window. Judge by what happens and where, not by a
single word. If nothing matches, return an empty list. Never invent windows.
"""

LOOK_SYSTEM = """\
You check one stretch of a film on a contact sheet: numbered tiles in reading
order, each labelled with its time. Decide whether the tag applies to what you
see. If it does, give the first and last tile that match, one plain sentence on
what happens, and the characters you can see (use names from the cast list when
you can tell). Be strict: a tag about a place or season must be visible.
"""


def _clock(t: float) -> str:
    s = int(round(t))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def windows(doc: dict[str, Any], size: float = WINDOW) -> list[tuple[float, float]]:
    """Consecutive automatic scenes joined into windows of about *size* seconds."""
    out: list[tuple[float, float]] = []
    start = None
    for sc in doc.get("scenes") or []:
        if start is None:
            start = sc["start"]
        if sc["end"] - start >= size:
            out.append((start, sc["end"]))
            start = None
    scenes = doc.get("scenes") or []
    if start is not None and scenes:
        out.append((start, scenes[-1]["end"]))
    return out


def window_text(doc: dict[str, Any], win: tuple[float, float]) -> str:
    lines = idx.dialogue(idx.lines_of(doc), win[0], win[1])
    return "\n".join(f"[{_clock(ln.start)}] {ln.text}" for ln in lines)


def batches(doc: dict[str, Any], wins: list[tuple[float, float]]) -> list[list[int]]:
    out: list[list[int]] = [[]]
    size = 0
    for i, w in enumerate(wins):
        n = len(window_text(doc, w)) + 40
        if out[-1] and size + n > BATCH_CHARS:
            out.append([])
            size = 0
        out[-1].append(i)
        size += n
    return [b for b in out if b]


def _parse(t: str, lo: float, hi: float) -> float:
    try:
        v = ai.parse_approx(t)
    except ai.AIError:
        return lo
    return min(max(v, lo), hi)


def cast_hint(cast: list[dict[str, Any]], n: int = 25) -> str:
    rows = [
        f"{c['character']} ({c['name']})" if c.get("character") else c["name"] for c in cast[:n]
    ]
    return "; ".join(rows)


def text_pass(
    cfg: AICfg,
    doc: dict[str, Any],
    title: str,
    tag: str,
    description: str,
    cast: list[dict[str, Any]],
    cancel: Any = None,
    progress: Callable[[float, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Candidate ranges from subtitles: [{start, end, why, people, confidence}]."""
    wins = windows(doc)
    found: list[dict[str, Any]] = []
    groups = batches(doc, wins)
    for gi, group in enumerate(groups):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(gi / max(1, len(groups)), f"Reading dialogue {gi + 1}/{len(groups)}")
        parts = []
        for i in group:
            text = window_text(doc, wins[i])
            if text:
                parts.append(f"## Window {i} ({_clock(wins[i][0])}-{_clock(wins[i][1])})\n{text}")
        if not parts:
            continue
        prompt = (
            f"Film: {title}\nTag: {tag}\nMeaning: {description or tag}\n"
            f"Cast: {cast_hint(cast)}\n\n" + "\n\n".join(parts)
        )
        hits = ai.ask_json(cfg, WindowHits, TEXT_SYSTEM, prompt)
        for h in hits.matches:
            if h.window not in group:
                continue
            lo, hi = wins[h.window]
            a, b = _parse(h.start, lo, hi), _parse(h.end, lo, hi)
            if b - a < 5:
                a, b = max(lo, a - 15), min(hi, a + 45)
            found.append(
                {"start": a, "end": b, "why": h.why, "people": h.people, "confidence": h.confidence}
            )
    return found


def look(
    cfg: AICfg, sheet: Any, title: str, tag: str, description: str, cast: list[dict[str, Any]]
) -> SheetCheck:
    prompt = (
        f"Film: {title}\nTag: {tag}\nMeaning: {description or tag}\n"
        f"Cast: {cast_hint(cast)}\nTiles: {sheet.legend()}"
    )
    return ai.ask_json(cfg, SheetCheck, LOOK_SYSTEM, prompt, images=[sheet.data])


def refine(cand: dict[str, Any], check: SheetCheck, times: list[float]) -> tuple[float, float]:
    """The candidate range narrowed to the matching tiles (half a slot either side)."""
    a, b = cand["start"], cand["end"]
    if not times:
        return a, b
    slot = (b - a) / len(times)
    first = min(max(check.first_tile, 1), len(times)) - 1
    last = min(max(check.last_tile, first + 1), len(times)) - 1
    return max(a, times[first] - slot / 2), min(b, times[last] + slot / 2)


def sweep_candidates(doc: dict[str, Any], max_lines: int = 1) -> list[dict[str, Any]]:
    """Automatic scenes with (almost) no dialogue, as candidates for the look pass."""
    lines = idx.lines_of(doc)
    out = []
    for sc in doc.get("scenes") or []:
        if (
            len(idx.dialogue(lines, sc["start"], sc["end"])) <= max_lines
            and sc["end"] - sc["start"] >= 8
        ):
            out.append(
                {
                    "start": sc["start"],
                    "end": sc["end"],
                    "why": "no dialogue: checked by eye",
                    "people": [],
                    "confidence": 0.3,
                }
            )
    return out


def merge(cands: list[dict[str, Any]], gap: float = 10.0) -> list[dict[str, Any]]:
    """Overlapping or nearly touching candidates joined into one."""
    out: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda c: c["start"]):
        if out and c["start"] <= out[-1]["end"] + gap:
            last = out[-1]
            last["end"] = max(last["end"], c["end"])
            last["people"] = list(dict.fromkeys([*last["people"], *c["people"]]))
            last["confidence"] = max(last["confidence"], c["confidence"])
            last["why"] = last["why"] or c["why"]
        else:
            out.append(dict(c))
    return out

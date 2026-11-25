"""The scene index of one film: camera cuts, subtitle lines, automatic scenes.

A scene is a run of shots that ends at a cut nobody is talking across: when
no subtitle line is on screen within ``QUIET`` seconds of a camera cut, the
story has probably moved on. Scenes shorter than ``MIN_SCENE`` keep growing;
anything longer than ``MAX_SCENE`` is cut at the quietest shot boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import subs

VERSION = 1
QUIET = 1.5
MIN_SCENE = 20.0
MAX_SCENE = 240.0
# ffmpeg scene score for a cut. 0.32 (Prompture's default) missed a quarter of
# the cuts of a real film; 0.2 and 0.12 found the same set.
CUT_THRESHOLD = 0.2


def _talking(lines: list[subs.Line], t: float, pad: float) -> bool:
    return any(ln.start - pad < t < ln.end + pad for ln in lines)


def group_scenes(
    shots: list[tuple[float, float]],
    lines: list[subs.Line],
    *,
    quiet: float = QUIET,
    min_len: float = MIN_SCENE,
    max_len: float = MAX_SCENE,
) -> list[dict[str, Any]]:
    """Shots -> scenes [{id, start, end, shots}] covering the same span."""
    if not shots:
        return []
    scenes: list[dict[str, Any]] = []
    start, count = shots[0][0], 0
    for i, (_a, b) in enumerate(shots):
        count += 1
        last = i == len(shots) - 1
        length = b - start
        if last or (length >= min_len and not _talking(lines, b, quiet)) or length >= max_len:
            scenes.append(
                {
                    "id": f"s{len(scenes) + 1:04d}",
                    "start": round(start, 3),
                    "end": round(b, 3),
                    "shots": count,
                }
            )
            start, count = b, 0
    return scenes


def dialogue(lines: list[subs.Line], start: float, end: float) -> list[subs.Line]:
    return [ln for ln in lines if ln.end > start and ln.start < end]


def file_identity(path: str) -> dict[str, Any]:
    st = Path(path).stat()
    return {"path": path, "size": st.st_size, "mtime": round(st.st_mtime, 3)}


def build(
    path: str,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    lang: str = "en",
    transcribe: bool = False,
    cancel: Any = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    """Index one film. Camera cuts read the whole file once (minutes for a 4K film)."""
    from prompture.media.understand import detect_shots, probe_video

    note = progress or (lambda _p, _m: None)
    note(0.02, "Reading subtitles")
    lines, sub_src = subs.load(path, ffmpeg, ffprobe, lang)
    if not lines and transcribe:
        note(0.05, "No text subtitles: transcribing the audio")
        from prompture.media.understand import transcribe as run_transcribe

        t = run_transcribe(path, language=lang, max_source_bytes=1 << 40)
        lines = subs.from_transcript(t.segments)
        sub_src = {"kind": "transcript", "model": t.model}
    info = probe_video(path)
    note(0.1, "Finding camera cuts")
    total = info.duration or 0.0

    def seen(pos: float) -> None:
        if total:
            note(
                0.1 + 0.85 * min(pos / total, 1.0),
                f"Finding camera cuts ({int(pos // 60)} min read)",
            )

    try:
        shots = detect_shots(path, cancel=cancel, progress=seen, threshold=CUT_THRESHOLD)
    except TypeError:  # Prompture before shot-detection progress
        shots = detect_shots(path, cancel=cancel, threshold=CUT_THRESHOLD)
    pairs = [(s.start, s.end) for s in shots]
    note(0.95, "Grouping scenes")
    return {
        "version": VERSION,
        "file": file_identity(path),
        "duration": info.duration,
        "width": info.width,
        "height": info.height,
        "subtitles": {**sub_src, "lines": len(lines)},
        "shots": [[round(a, 3), round(b, 3)] for a, b in pairs],
        "lines": [ln.to_dict() for ln in lines],
        "scenes": group_scenes(pairs, lines),
    }


def lines_of(doc: dict[str, Any]) -> list[subs.Line]:
    return [subs.Line(x["start"], x["end"], x["text"]) for x in doc.get("lines") or []]


def snap(doc: dict[str, Any], t: float, max_move: float = 3.0) -> float:
    """*t* moved to the nearest camera cut within *max_move* seconds."""
    best, dist = t, max_move
    for a, b in doc.get("shots") or []:
        for edge in (a, b):
            if abs(edge - t) <= dist:
                best, dist = edge, abs(edge - t)
    return round(best, 3)


QUIET_REACH = 20.0  # how far an edge may move to avoid cutting someone off
SNAP = 3.0  # an edge this close to a cut moves onto it


def _cuts(doc: dict[str, Any]) -> list[float]:
    return sorted({edge for shot in doc.get("shots") or [] for edge in shot})


def _quiet_edge(doc: dict[str, Any], t: float, reach: float, later: bool) -> float:
    lines = lines_of(doc)
    cuts = _cuts(doc)
    # A cut within snapping distance comes first, nearest first; then cuts further
    # out in the safe direction (later for an end, earlier for a start).
    near = sorted((c for c in cuts if abs(c - t) <= SNAP), key=lambda c: abs(c - t))
    far = [c for c in cuts if (t + SNAP < c <= t + reach if later else t - reach <= c < t - SNAP)]
    for c in near + (far if later else far[::-1]):
        if not _talking(lines, c, 0.3):
            return round(c, 3)
    # No quiet cut in reach (people talk straight through): stop in the first
    # pause between lines instead, just after a line ends (or before one starts).
    if _talking(lines, t, 0.0):
        if later:
            gaps = sorted(ln.end + 0.3 for ln in lines if t <= ln.end + 0.3 <= t + reach)
        else:
            gaps = sorted(
                (ln.start - 0.3 for ln in lines if t - reach <= ln.start - 0.3 <= t), reverse=True
            )
        for g in gaps:
            if not _talking(lines, g, 0.25):
                return round(g, 3)
    return snap(doc, t)


def quiet_end(doc: dict[str, Any], t: float, reach: float = QUIET_REACH) -> float:
    """A camera cut near *t*, or up to *reach* later, that nobody talks across.

    An end chosen by looking at frames can fall mid-sentence; a supercut that
    stops there sounds broken. When every cut in reach has someone talking, it
    ends in the first pause between lines instead; with no pause either, on
    the nearest cut.
    """
    return _quiet_edge(doc, t, reach, later=True)


def quiet_start(doc: dict[str, Any], t: float, reach: float = QUIET_REACH) -> float:
    """A camera cut near *t*, or up to *reach* earlier, that nobody talks across."""
    return _quiet_edge(doc, t, reach, later=False)

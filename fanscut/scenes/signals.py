"""Measured signals (docs/markers.md section 3): no AI, numbers only.

Signals are not tags: nobody reviews them and the app never shows them as
chips. They sit with the film's index and help pick posters, extend cuts and
find stretches (a quiet wide shot, the end of a line).

One pass over the picture (one frame a second, small) gives brightness,
movement and faces; one pass over the sound gives loudness; the index gives
shots and subtitle lines. From those:

=================  ==================================================
no-face         no face in any sampled frame of a shot
close-up        one large face in most frames of a shot
group           three or more faces in most frames of a shot
dialogue        subtitle speech (sound descriptions left out)
fast-cutting    30 or more camera cuts a minute
long-take       one shot of 20 s or more
dark            a very dark shot
still           a shot that barely moves
fade            black frames at a shot edge
silence         near silence for 3 s or more
loud            the loudest peaks of the film
=================  ==================================================

Neighbouring shots with the same signal join into one range. Ranges are at
least ``MIN_RANGE`` and at most ``MAX_RANGE`` seconds (the marker file's
limits).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import index as idx

VERSION = 1
FPS = 1.0  # frames sampled a second
WIDTH = 640  # sampled frame width
RATE = 8000  # audio sample rate for loudness
STEP = 0.5  # seconds per loudness value

MIN_RANGE = 3.0
MAX_RANGE = 900.0
BRIDGE = 2.0  # an unsampled shot this short doesn't break a run

DARK = 15.0  # mean luma (0-255) below this is dark (a dark film has a median near 30)
BLACK = 12.0  # a frame this dark is black (a fade)
STILL = 2.5  # mean frame difference (0-255, 64x36 grey) below this is still
CLOSE = 0.28  # a face this share of the frame height is a close-up
FAST = 30.0  # camera cuts a minute
FAST_SPAN = 10.0  # seconds around a shot that count its neighbours
LONG = 20.0  # seconds for a long take
SILENT_DB = -50.0
LOUD_DB = -12.0  # peaks must be at least this loud...
LOUD_PCT = 99.5  # ...and in the film's top half percent
DIALOGUE_GAP = 2.5
DIALOGUE_MIN = 6.0

MEASURED = (
    "no-face", "close-up", "group", "dialogue", "fast-cutting", "long-take",
    "dark", "still", "fade", "silence", "loud",
)  # fmt: skip

_NOT_SPEECH = re.compile(r"^[\s\-]*([\[(].*[\])]|[♪♫#*].*)[\s\-]*$")


@dataclass
class Sample:
    t: float
    luma: float
    thumb: Any  # 64x36 grey, float32
    faces: int | None = None  # None: faces weren't measured
    face_h: float = 0.0  # tallest face as a share of the frame height


# -- reading the film -----------------------------------------------------------------
def frames(
    path: str,
    width: int,
    height: int,
    *,
    ffmpeg: str = "ffmpeg",
    fps: float = FPS,
    cancel: Any = None,
) -> Iterator[tuple[float, Any]]:
    """(time, BGR frame) at *fps*, scaled to *width* x *height*, in one decode."""
    argv = [
        ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:v:0", "-an", "-sn",
        "-vf", f"fps={fps},scale={width}:{height}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]  # fmt: skip
    size = width * height * 3
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)  # noqa: S603
    assert proc.stdout is not None
    try:
        i = 0
        while True:
            if cancel is not None and cancel.is_set():
                return
            buf = proc.stdout.read(size)
            if len(buf) < size:
                return
            yield i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.kill()
        proc.wait()


def levels(path: str, *, ffmpeg: str = "ffmpeg", step: float = STEP) -> Any:
    """Loudness in dBFS for every *step* seconds of the first audio track."""
    argv = [
        ffmpeg, "-v", "error", "-nostdin", "-i", path, "-map", "0:a:0", "-vn", "-sn",
        "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-",
    ]  # fmt: skip
    proc = subprocess.run(argv, capture_output=True, timeout=1800, check=False)  # noqa: S603
    pcm = np.frombuffer(proc.stdout, np.int16).astype(np.float32) / 32768.0
    n = int(RATE * step)
    if len(pcm) < n:
        return np.zeros(0, np.float32)
    blocks = pcm[: len(pcm) // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(blocks**2, axis=1))
    return (20 * np.log10(np.maximum(rms, 1e-6))).astype(np.float32)


def measure(img: Any, engine: Any = None) -> tuple[float, Any, int | None, float]:
    """(mean luma, 64x36 grey thumb, face count or None, tallest face share)."""
    grey = img.mean(axis=2) if img.ndim == 3 else img.astype(np.float32)
    h, w = grey.shape
    thumb = grey[: h - h % 36, : w - w % 64].reshape(36, (h - h % 36) // 36, 64, -1).mean((1, 3))
    faces, face_h = None, 0.0
    if engine is not None:
        found = engine.detect(img)
        faces = len(found)
        face_h = max((float(f[3]) / h for f in found), default=0.0)
    return float(grey.mean()), thumb.astype(np.float32), faces, face_h


# -- turning numbers into ranges ------------------------------------------------------
def _runs(shots: list[tuple[float, float]], marks: list[bool | None]) -> list[tuple[float, float]]:
    """Ranges of consecutive marked shots; an unknown (None) shot up to BRIDGE long
    joins its neighbours."""
    out: list[tuple[float, float]] = []
    cur: list[float] | None = None
    pending: float | None = None  # end of bridged unknown shots
    for (a, b), m in zip(shots, marks, strict=True):
        if m:
            if cur is None:
                cur = [a, b]
            else:
                cur[1] = b
            pending = None
        elif m is None and cur is not None and b - a <= BRIDGE:
            pending = b
        else:
            if cur is not None:
                out.append((cur[0], cur[1]))
            cur, pending = None, None
    if cur is not None:
        out.append((cur[0], cur[1]))
    del pending
    return out


def _spans(mask: Any, step: float, min_len: float) -> list[tuple[float, float]]:
    out = []
    start = None
    for i, on in enumerate([*mask, False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            if (i - start) * step >= min_len:
                out.append((round(start * step, 3), round(i * step, 3)))
            start = None
    return out


def _join(ranges: list[tuple[float, float]], gap: float) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(x[0], x[1]) for x in out]


def _fit(ranges: list[tuple[float, float]], duration: float | None) -> list[tuple[float, float]]:
    """Clip to the film, drop short ranges, split long ones."""
    out = []
    for a, b in ranges:
        a = max(0.0, a)
        if duration:
            b = min(b, duration)
        while b - a > MAX_RANGE:
            out.append((round(a, 3), round(a + MAX_RANGE, 3)))
            a += MAX_RANGE
        if b - a >= MIN_RANGE:
            out.append((round(a, 3), round(b, 3)))
    return out


def speech(text: str) -> bool:
    return not _NOT_SPEECH.match(text.strip())


def label(
    doc: dict[str, Any], samples: list[Sample], loud: Any | None = None
) -> dict[str, list[tuple[float, float]]]:
    """Every signal's ranges for one film."""
    duration = doc.get("duration")
    shots = [(float(a), float(b)) for a, b in doc.get("shots") or []]
    out: dict[str, list[tuple[float, float]]] = {}

    # per shot: the samples inside it
    per: list[list[Sample]] = [[] for _ in shots]
    j = 0
    for s in sorted(samples, key=lambda s: s.t):
        while j < len(shots) and s.t >= shots[j][1]:
            j += 1
        if j < len(shots) and s.t >= shots[j][0]:
            per[j].append(s)

    def mark(test: Callable[[list[Sample]], bool]) -> list[bool | None]:
        return [test(ss) if ss else None for ss in per]

    faced = [s for s in samples if s.faces is not None]
    if faced:
        out["no-face"] = _runs(shots, mark(lambda ss: all(s.faces == 0 for s in ss)))
        out["close-up"] = _runs(
            shots,
            mark(lambda ss: sum(s.faces == 1 and s.face_h >= CLOSE for s in ss) * 2 > len(ss)),
        )
        out["group"] = _runs(
            shots, mark(lambda ss: sum((s.faces or 0) >= 3 for s in ss) * 2 > len(ss))
        )
    out["dark"] = _runs(shots, mark(lambda ss: float(np.mean([s.luma for s in ss])) < DARK))

    def still(ss: list[Sample]) -> bool | None:
        if len(ss) < 3:
            return None if len(ss) < 2 else False
        diffs = [float(np.abs(a.thumb - b.thumb).mean()) for a, b in zip(ss, ss[1:], strict=False)]
        return float(np.mean(diffs)) < STILL and all(s.luma >= BLACK for s in ss)

    out["still"] = _runs(shots, [still(ss) if ss else None for ss in per])

    fades = []
    edges = sorted({e for shot in shots for e in shot})
    for s in samples:
        if s.luma < BLACK and edges:
            near = min(edges, key=lambda e: abs(e - s.t))
            if abs(near - s.t) <= 1.5:
                fades.append((s.t - 1.5, s.t + 1.5))
    out["fade"] = _join(fades, 0.5)

    out["long-take"] = [(a, b) for a, b in shots if b - a >= LONG]

    fast = []
    for a, b in shots:
        mid = (a + b) / 2
        lo, hi = mid - FAST_SPAN / 2, mid + FAST_SPAN / 2
        cuts = sum(1 for e in edges if lo < e < hi)
        fast.append(cuts * 60 / FAST_SPAN >= FAST)
    out["fast-cutting"] = [r for r in _runs(shots, list(fast)) if r[1] - r[0] >= DIALOGUE_MIN]

    talk = [(ln.start, ln.end) for ln in idx.lines_of(doc) if speech(ln.text)]
    out["dialogue"] = [r for r in _join(talk, DIALOGUE_GAP) if r[1] - r[0] >= DIALOGUE_MIN]

    if loud is not None and len(loud):
        out["silence"] = _spans(loud < SILENT_DB, STEP, MIN_RANGE)
        peak = max(LOUD_DB, float(np.percentile(loud, LOUD_PCT)))
        hits = [
            (i * STEP - 1.5, (i + 1) * STEP + 1.5) for i in np.flatnonzero(loud >= peak).tolist()
        ]
        out["loud"] = _join(hits, 1.0)

    return {tag: _fit(rs, duration) for tag, rs in out.items()}

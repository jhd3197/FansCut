"""Subtitles that follow the cut: the user's own text tracks, re-timed.

Each source's text subtitle tracks are read with ffmpeg (never stored in a
recipe), every cue is mapped through the cut list (segment start, speed) and
the results go next to the output as Plex sidecars:

    <Title>.eng.srt, <Title>.eng.forced.srt, <Title>.eng.sdh.srt

Tracks are matched across sources by (language, forced, SDH), so the
Hobbit's forced Elvish lines stay a forced track. Image subtitles (PGS,
VobSub) can't be re-timed as text; they are reported, not dropped silently.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import FFmpegCfg
from ..models import Segment

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _t(s: str) -> float:
    m = _TIME.match(s.strip())
    if not m:
        raise ValueError(s)
    h, mi, se, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + se + ms / 1000


def _fmt(t: float) -> str:
    ms = max(round(t * 1000), 0)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.strip().splitlines()
        i = next((n for n, line in enumerate(lines) if "-->" in line), None)
        if i is None:
            continue
        try:
            a, b = (x.strip().split(" ")[0] for x in lines[i].split("-->"))
            cues.append(Cue(_t(a), _t(b), "\n".join(lines[i + 1:]).strip()))
        except ValueError:
            continue
    return cues


def write_srt(cues: list[Cue]) -> str:
    return "\n".join(f"{n}\n{_fmt(c.start)} --> {_fmt(c.end)}\n{c.text}\n"
                     for n, c in enumerate(cues, start=1))


def retime(segments: list[tuple[float, float, float, float, list[Cue]]]) -> list[Cue]:
    """segments: (out_start, src_start, src_end, speed, cues of that source) in order."""
    out = []
    for t0, a, b, speed, cues in segments:
        for c in cues:
            s, e = max(c.start, a), min(c.end, b)
            if e - s < 0.05:
                continue
            out.append(Cue(t0 + (s - a) / speed, t0 + (e - a) / speed, c.text))
    return out


def track_key(t: dict[str, Any]) -> tuple[str, bool, bool]:
    title = (t.get("title") or "").lower()
    return ((t.get("language") or "und").lower(), bool(t.get("forced")),
            "sdh" in title or "hearing" in title)


def sidecar_name(stem: str, key: tuple[str, bool, bool]) -> str:
    lang, forced, sdh = key
    return f"{stem}.{lang}{'.sdh' if sdh else ''}{'.forced' if forced else ''}.srt"


def extract(ff: FFmpegCfg, path: str, index: int) -> list[Cue]:
    proc = subprocess.run([ff.binary, "-v", "error", "-i", path, "-map", f"0:{index}",
                           "-f", "srt", "-"], capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        return []
    return parse_srt(proc.stdout.decode("utf-8", "replace"))


def write_sidecars(ff: FFmpegCfg, segs: list[Segment], output: Path,
                   probe: Any) -> tuple[list[str], list[str]]:
    """Re-time every text track through the cut. Returns (files written, warnings)."""
    tracks: dict[str, dict[tuple[str, bool, bool], int]] = {}
    image: set[str] = set()
    for f in dict.fromkeys(s.file for s in segs):
        try:
            info = probe(f)
        except Exception:  # noqa: BLE001 - subtitles are a nicety
            continue
        for t in info.get("subtitle_tracks") or []:
            if t.get("codec") in TEXT_CODECS and t.get("index") is not None:
                tracks.setdefault(f, {}).setdefault(track_key(t), int(t["index"]))
            else:
                image.add(Path(f).name)
    keys = {k for per in tracks.values() for k in per}
    cache: dict[tuple[str, int], list[Cue]] = {}
    written, t0, plan = [], 0.0, []
    for s in segs:
        plan.append((t0, s))
        t0 += s.duration
    for key in sorted(keys):
        pieces = []
        for start, s in plan:
            idx = tracks.get(s.file, {}).get(key)
            if idx is None:
                continue
            if (s.file, idx) not in cache:
                cache[(s.file, idx)] = extract(ff, s.file, idx)
            pieces.append((start, s.start, s.end, (s.fx or {}).get("speed", 1.0),
                           cache[(s.file, idx)]))
        cues = retime(pieces)
        if not cues:
            continue
        dst = output.with_name(sidecar_name(output.stem, key))
        dst.write_text(write_srt(cues), encoding="utf-8")
        written.append(str(dst))
    warnings = ([f"image subtitles (PGS/VobSub) in {', '.join(sorted(image))} can't be re-timed "
                 "as text; they are not carried over"] if image else [])
    return written, warnings

"""Timed subtitle lines for a film: a sidecar SRT, or a text track in the file.

Image subtitles (PGS, VobSub) can't be read as text; those films fall back to
transcription when the caller asks for it.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")
_TAG = re.compile(r"<[^>]+>|\{[^}]*\}")
ENGLISH = {"en", "eng", "english"}


@dataclass
class Line:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


def _secs(m: re.Match[str]) -> float:
    h, mi, s, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[Line]:
    """SRT text -> lines (formatting tags dropped, empty cues skipped)."""
    out: list[Line] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.replace("﻿", "")):
        rows = [r for r in block.strip().splitlines() if r.strip()]
        for i, row in enumerate(rows):
            if "-->" in row:
                times = list(_TIME.finditer(row))
                if len(times) < 2:
                    break
                body = " ".join(_TAG.sub("", r).strip() for r in rows[i + 1 :])
                body = re.sub(r"\s+", " ", body).strip()
                if body:
                    out.append(Line(_secs(times[0]), _secs(times[1]), body))
                break
    out.sort(key=lambda ln: ln.start)
    return out


def _lang(tag: str) -> str:
    return (tag or "").lower().split("-")[0].split("_")[0]


def sidecars(video: str, lang: str = "en") -> list[Path]:
    """SRT files next to the video whose name starts with its stem, best first."""
    v = Path(video)
    if not v.parent.is_dir():
        return []
    found = [
        p for p in v.parent.iterdir() if p.suffix.lower() == ".srt" and p.name.startswith(v.stem)
    ]

    def rank(p: Path) -> tuple[int, int, str]:
        parts = [x.lower() for x in p.name[len(v.stem) :].split(".") if x]
        has_lang = any(
            _lang(x) in ({lang} | (ENGLISH if lang in ENGLISH else set())) for x in parts
        )
        forced = any(x in ("forced", "signs") for x in parts)
        return (0 if has_lang else 1 if len(parts) <= 1 else 2, 1 if forced else 0, p.name)

    return sorted(found, key=rank)


def text_streams(ffprobe: str, video: str, timeout: float = 60) -> list[dict[str, Any]]:
    """Text subtitle streams of a file: [{index, codec, language, title, forced}]."""
    proc = subprocess.run(  # noqa: S603 - argv list
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_name:stream_tags=language,title:stream_disposition=forced",
            "-of",
            "json",
            video,
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode:
        return []
    try:
        doc = json.loads(proc.stdout or b"{}")
    except json.JSONDecodeError:
        return []
    out = []
    for n, s in enumerate(doc.get("streams") or []):
        if (s.get("codec_name") or "") not in TEXT_CODECS:
            continue
        tags = s.get("tags") or {}
        out.append(
            {
                "index": n,
                "codec": s.get("codec_name"),
                "language": _lang(tags.get("language", "")),
                "title": tags.get("title", ""),
                "forced": bool((s.get("disposition") or {}).get("forced")),
            }
        )
    return out


def pick_stream(streams: list[dict[str, Any]], lang: str = "en") -> dict[str, Any] | None:
    """The best full-dialogue track: wanted language, not forced, not 'signs'."""
    wanted = {lang} | (ENGLISH if lang in ENGLISH else set())

    def rank(s: dict[str, Any]) -> tuple[int, int, int]:
        title = (s.get("title") or "").lower()
        partial = s["forced"] or "forced" in title or "sign" in title
        sdh = "sdh" in title or "hearing" in title
        return (
            0 if s["language"] in wanted else 1 if not s["language"] else 2,
            1 if partial else 0,
            1 if sdh else 0,
        )

    return min(streams, key=rank) if streams else None


def extract_stream(ffmpeg: str, video: str, sub_index: int, timeout: float = 300) -> str:
    """One subtitle track (``0:s:<sub_index>``) converted to SRT text."""
    proc = subprocess.run(  # noqa: S603 - argv list
        [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-i",
            video,
            "-map",
            f"0:s:{sub_index}",
            "-f",
            "srt",
            "-",
        ],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or b"").decode("utf-8", "replace")[-300:])
    return (proc.stdout or b"").decode("utf-8", "replace")


VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".webm"}
SAME_CUT = 1.5  # seconds: a sibling file this close in length is the same cut


def duration(ffprobe: str, video: str, timeout: float = 60) -> float | None:
    proc = subprocess.run(  # noqa: S603 - argv list
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video],
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    try:
        return (
            float((proc.stdout or b"").decode().strip() or "nan") if not proc.returncode else None
        )
    except ValueError:
        return None


def siblings(video: str) -> list[Path]:
    """Other video files in the same folder (another copy of the same film)."""
    v = Path(video)
    if not v.parent.is_dir():
        return []
    return sorted(p for p in v.parent.iterdir() if p != v and p.suffix.lower() in VIDEO_EXT)


def _from_file(
    video: str, ffmpeg: str, ffprobe: str, lang: str
) -> tuple[list[Line], dict[str, Any]] | None:
    for p in sidecars(video, lang):
        lines = parse_srt(p.read_text(encoding="utf-8", errors="replace"))
        if lines:
            return lines, {"kind": "sidecar", "file": p.name}
    stream = pick_stream(text_streams(ffprobe, video), lang)
    if stream is not None:
        lines = parse_srt(extract_stream(ffmpeg, video, stream["index"]))
        if lines:
            return lines, {
                "kind": "embedded",
                "track": stream["index"],
                "language": stream["language"],
                "title": stream["title"],
            }
    return None


def load(
    video: str, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", lang: str = "en"
) -> tuple[list[Line], dict[str, Any]]:
    """Subtitle lines for *video* and where they came from ({"kind": "none"} when none).

    When the file has none, another copy in the same folder with the same length
    (an .mkv next to an .mp4) lends its subtitles: same cut, same timings.
    """
    found = _from_file(video, ffmpeg, ffprobe, lang)
    if found:
        return found
    others = siblings(video)
    if others:
        mine = duration(ffprobe, video)
        for other in others:
            theirs = duration(ffprobe, str(other))
            if mine and theirs and abs(mine - theirs) <= SAME_CUT:
                found = _from_file(str(other), ffmpeg, ffprobe, lang)
                if found:
                    lines, src = found
                    return lines, {**src, "kind": f"{src['kind']} (from {other.name})"}
    return [], {"kind": "none"}


def from_transcript(segments: list[Any]) -> list[Line]:
    """Prompture transcript segments -> lines."""
    return [
        Line(float(s.start), float(s.end), str(s.text).strip())
        for s in segments
        if str(s.text).strip()
    ]

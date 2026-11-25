"""Turn a cut list into ffmpeg commands without running anything.

Fast path: stream copy through the concat demuxer (inpoint/outpoint). That cuts
on keyframes, so edges can be a little loose, and it needs every file to share
codecs and shape. Otherwise one filter_complex trims and joins everything and
re-encodes (libx264, or NVENC if configured), which is frame accurate.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..config import FFmpegCfg
from ..models import Segment
from ..planner import CutPlan

FADE = 0.5
Mode = Literal["auto", "copy", "encode"]


@dataclass
class RenderPlan:
    mode: Literal["copy", "encode"]
    reason: str
    output: str  # final path
    output_tmp: str  # what ffmpeg writes before the rename
    command: list[str]
    files: dict[str, str]  # work-dir file name -> contents
    total_seconds: float
    chapters: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cache_key: str = ""  # content hash; set by Core when the output name is fixed

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "reason": self.reason, "output": self.output,
            "command": self.command, "files": self.files,
            "total_seconds": round(self.total_seconds, 3), "chapters": self.chapters,
            "warnings": self.warnings, "cache_key": self.cache_key,
        }


def safe_name(title: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]+', " ", title)
    return cleaned.strip(" .") or "fan cut"


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    p = directory / f"{stem}.{ext}"
    n = 2
    while p.exists():
        p = directory / f"{stem} ({n}).{ext}"
        n += 1
    return p


def _concat_quote(path: str) -> str:
    # the demuxer resolves relative entries against the list file, not the cwd
    return "'" + os.path.abspath(path).replace("'", "'\\''") + "'"


def _meta_escape(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def _join_adjacent(segs: list[Segment]) -> list[Segment]:
    out: list[Segment] = []
    for s in segs:
        if (out and out[-1].file == s.file and abs(out[-1].end - s.start) < 0.001
                and s.xfade_ms == 0):
            out[-1] = Segment(out[-1].episode_id, s.file, out[-1].start, s.end, out[-1].reason)
        else:
            out.append(s)
    return out


def chapters_for(segs: list[Segment], titles: dict[str, str]) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    t = 0.0
    for s in segs:
        if not chapters or chapters[-1]["episode_id"] != s.episode_id:
            chapters.append({"episode_id": s.episode_id,
                             "title": titles.get(s.episode_id, s.episode_id),
                             "start": t, "end": t})
        t += s.duration
        chapters[-1]["end"] = t
    return chapters


def _ffmetadata(chapters: list[dict[str, Any]]) -> str:
    lines = [";FFMETADATA1"]
    for c in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={round(c['start'] * 1000)}",
                  f"END={round(c['end'] * 1000)}", f"title={_meta_escape(c['title'])}"]
    return "\n".join(lines) + "\n"


def compatible(infos: list[dict[str, Any]]) -> str | None:
    """None if every file can be stream-copied together, else why not."""
    def shape(i: dict[str, Any]) -> tuple[Any, ...]:
        v, a = i.get("video") or {}, i.get("audio") or {}
        return (v.get("codec"), v.get("width"), v.get("height"), v.get("pix_fmt"),
                round(v.get("fps") or 0, 2), a.get("codec"), a.get("sample_rate"),
                a.get("channels"))
    if any("video" not in i for i in infos):
        return "a file has no video stream"
    if len({shape(i) for i in infos}) > 1:
        return "files differ in codec, size, frame rate or audio format"
    return None


def _encoder_args(ff: FFmpegCfg) -> list[str]:
    if ff.nvenc:
        v = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", str(ff.crf)]
    else:
        v = ["-c:v", "libx264", "-preset", ff.preset, "-crf", str(ff.crf)]
    return [*v, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", ff.audio_bitrate]


def _filter_script(segs: list[Segment], inputs: dict[str, int], first: dict[str, Any],
                   audio: bool, fade: bool, audio_map: dict[str, int] | None = None,
                   infos: dict[str, Any] | None = None) -> str:
    w, h = first.get("width") or 1920, first.get("height") or 1080
    fps = round(first.get("fps") or 24, 3) or 24
    lines, vlabels, alabels = [], [], []
    # A crossfade lets the outgoing audio run d seconds past its video cut (the
    # content exists in the source file), so audio and video stay in sync.
    ext = [0.0] * len(segs)
    for k in range(1, len(segs)):
        if segs[k].xfade_ms > 0 and infos:
            prev = segs[k - 1]
            room = (infos[prev.file].get("duration") or prev.end) - prev.end
            ext[k - 1] = min(segs[k].xfade_ms / 1000, max(room, 0.0))
    for k, s in enumerate(segs):
        n = inputs[s.file]
        a_idx = (audio_map or {}).get(s.file, 0)
        vf = (f"[{n}:v:0]trim=start={s.start:.3f}:end={s.end:.3f},setpts=PTS-STARTPTS,"
              f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,"
              f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
        af = (f"[{n}:a:{a_idx}]atrim=start={s.start:.3f}:end={s.end + ext[k]:.3f},"
              "asetpts=PTS-STARTPTS,aresample=48000,"
              "aformat=sample_fmts=fltp:channel_layouts=stereo")
        if fade:
            d = round(min(FADE, s.duration / 2), 3)
            starts = k > 0 and segs[k - 1].episode_id != s.episode_id
            ends = k < len(segs) - 1 and segs[k + 1].episode_id != s.episode_id
            if starts:
                vf += f",fade=t=in:st=0:d={d}"
                af += f",afade=t=in:st=0:d={d}"
            if ends:
                vf += f",fade=t=out:st={s.duration - d:.3f}:d={d}"
                af += f",afade=t=out:st={s.duration - d:.3f}:d={d}"
        lines.append(f"{vf}[v{k}];")
        vlabels.append(f"[v{k}]")
        if audio:
            lines.append(f"{af}[a{k}];")
            alabels.append((f"[a{k}]", s.xfade_ms))
    lines.append(f"{''.join(vlabels)}concat=n={len(segs)}:v=1:a=0[v];")
    if not audio:
        return "\n".join(lines)[:-1] + "\n"
    # audio chain: pairwise concat for hard cuts, acrossfade where asked
    cur = alabels[0][0]
    for k, (label, xfade) in enumerate(alabels[1:], start=1):
        nxt = f"[ac{k}]"
        if xfade > 0:
            lines.append(f"{cur}{label}acrossfade=d={xfade / 1000:.3f}{nxt};")
        else:
            lines.append(f"{cur}{label}concat=n=2:v=0:a=1{nxt};")
        cur = nxt
    if len(alabels) == 1:
        lines.append(f"{alabels[0][0]}anull[a]")
    else:
        lines.append(f"{cur}anull[a]")
    return "\n".join(lines) + "\n"


def build_plan(
    cut: CutPlan,
    ff: FFmpegCfg,
    output: Path,
    workdir: Path,
    probe: Callable[[str], dict[str, Any]],
    titles: dict[str, str] | None = None,
    mode: Mode = "auto",
    audio_map: dict[str, int] | None = None,
) -> RenderPlan:
    segs = _join_adjacent(cut.segments)
    if not segs:
        raise ValueError("the cut is empty: nothing to render")
    infos = {f: probe(f) for f in dict.fromkeys(s.file for s in segs)}
    first = infos[segs[0].file].get("video") or {}
    audio = all("audio" in i for i in infos.values())
    warnings = [] if audio else ["a file has no audio stream, so the output has no audio"]
    fade = cut.options.transition == "fade"
    # the concat demuxer cannot pick a different audio track per file
    track_pick = any(i > 0 for i in (audio_map or {}).values())
    xfade = any(s.xfade_ms > 0 for s in segs)  # audio crossfades need the filter graph

    why = compatible(list(infos.values()))
    if mode == "copy" and (why or fade or track_pick or xfade):
        blockers = [why, "fade needs re-encoding" if fade else None,
                    "audio track selection needs re-encoding" if track_pick else None,
                    "audio crossfades need re-encoding" if xfade else None]
        raise ValueError(f"cannot stream copy: {next(b for b in blockers if b)}")
    if mode == "auto":
        why = why or ("fade transitions need re-encoding" if fade else None)
        why = why or ("audio track selection needs re-encoding" if track_pick else None)
        why = why or ("audio crossfades need re-encoding" if xfade else None)
        mode = "encode" if why else "copy"
    elif mode == "encode":
        why = "re-encode requested"
    reason = "stream copy (files match)" if mode == "copy" else (why or "")
    chapters = chapters_for(segs, titles or {})
    total = sum(s.duration for s in segs)
    tmp = output.with_name(f"{output.stem}.part{output.suffix}")
    base = [ff.binary, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats"]
    meta = ["-f", "ffmetadata", "-i", str(workdir / "chapters.ffmeta")]
    files = {"chapters.ffmeta": _ffmetadata(chapters)}
    tail = ["-movflags", "+faststart"] if output.suffix == ".mp4" else []

    if mode == "copy":
        listing = []
        for s in segs:
            listing += [f"file {_concat_quote(s.file)}", f"inpoint {s.start:.3f}",
                        f"outpoint {s.end:.3f}"]
        files["concat.txt"] = "\n".join(listing) + "\n"
        cmd = [*base, "-f", "concat", "-safe", "0", "-i", str(workdir / "concat.txt"), *meta,
               "-map", "0:v:0", *(["-map", "0:a:0"] if audio else []),
               "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", *tail, str(tmp)]
    else:
        inputs = {f: n for n, f in enumerate(infos)}
        files["filter.txt"] = _filter_script(segs, inputs, first, audio, fade, audio_map, infos)
        cmd = [*base]
        for f in inputs:
            cmd += ["-i", f]
        cmd += [*meta, "-filter_complex_script", str(workdir / "filter.txt"), "-map", "[v]",
                *(["-map", "[a]"] if audio else []),
                "-map_metadata", f"{len(inputs)}", "-map_chapters", f"{len(inputs)}",
                *_encoder_args(ff), *tail, str(tmp)]
    return RenderPlan(mode, reason, str(output), str(tmp), cmd, files, total, chapters, warnings)

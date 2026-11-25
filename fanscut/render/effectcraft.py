"""EffectCraft for effect segments: grade (and later Roto Brush) through effectcraft-cli.

Plain segments never come here, and neither do operations ffmpeg already does
well (speed, fades, crop, titles…). A segment whose grade is routed to
EffectCraft is:

  a. cut out with handles into something EffectCraft reads: HEVC Main10 MP4
     (stream copy when the source is HEVC, else a 10-bit re-encode) plus WAV.
     It can't read HEVC-in-MKV, E-AC3 or TrueHD; its Matroska support is
     VP9/AV1/Opus only.
  b. turned into an EffectCraft project by engine commands (no .ecproj
     template, no raw values from a recipe: only FansCut's validated numbers),
     with Lumetri Color for the grade, in a Rec.2100 PQ / HLG output space when
     the source is HDR.
  c. rendered with `effectcraft-cli render` to HEVC Main10, then joined back:
     the rendered picture replaces the segment's video and the original file
     still supplies its audio.

Recipes never contain .ecproj or .aep; FansCut translates its own effect
list at render time.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import FFmpegCfg
from ..errors import FFmpegError
from ..models import Segment
from . import ffmpeg

HANDLE = 1.0  # seconds kept either side, so EffectCraft has context at the edges


class EffectCraftError(FFmpegError):
    pass


def lumetri(grade: dict[str, float]) -> dict[str, float]:
    """FansCut grade → Lumetri Color parameters (paths under effects/#1)."""
    out = {}
    if "exposure" in grade:
        out["basicCorrection/tone/exposure"] = round(grade["exposure"], 4)  # stops, both
    if "contrast" in grade:
        out["basicCorrection/tone/contrast"] = round(max(-100, min(100, (grade["contrast"] - 1)
                                                                    * 100)), 2)
    if "saturation" in grade:
        out["basicCorrection/saturation"] = round(grade["saturation"] * 100, 2)
    if "temperature" in grade:
        out["basicCorrection/whiteBalance/temperature"] = round(grade["temperature"] * 2, 2)
    return out


def output_space(info: dict[str, Any]) -> str:
    t = (info.get("video") or {}).get("color_transfer")
    return {"smpte2084": "rec2100pq", "arib-std-b67": "rec2100hlg"}.get(t or "", "rec709")


def project_commands(piece: Path, info: dict[str, Any], grade: dict[str, float]
                     ) -> list[dict[str, Any]]:
    """The engine commands that build the project: settings, footage + comp, Lumetri."""
    space = output_space(info)
    steps: list[dict[str, Any]] = [
        {"command": "file.projectSettings",
         "params": {"bitDepth": 32, "workingSpace": "rec2020" if space != "rec709" else "rec709",
                    "outputSpace": space}},
        {"command": "file.import", "params": {"paths": [str(piece)], "newComp": True}},
        {"command": "effect.apply", "params": {"effect": "ec.color.lumetri"}},
    ]
    for path, value in lumetri(grade).items():
        steps.append({"command": "prop.set",
                      "params": {"path": f"effects/#1/{path}", "value": value}})
    return steps


def cut_piece(ff: FFmpegCfg, seg: Segment, info: dict[str, Any], workdir: Path,
              name: str) -> tuple[Path, float]:
    """HEVC MP4 of [start - HANDLE, end + HANDLE]; returns (file, offset of seg.start in it)."""
    a = max(seg.start - HANDLE, 0.0)
    b = seg.end + HANDLE
    out = workdir / f"{name}.mp4"
    v = info.get("video") or {}
    if v.get("codec") == "hevc":
        # stream copy keeps every bit (and the HDR SEI); the cut lands on a keyframe
        # at or before `a`, so measure where seg.start ends up
        args = ["-ss", f"{a:.3f}", "-i", seg.file, "-t", f"{b - a + 2:.3f}", "-map", "0:v:0",
                "-c:v", "copy", "-tag:v", "hvc1"]
    else:
        args = ["-ss", f"{a:.3f}", "-i", seg.file, "-t", f"{b - a:.3f}", "-map", "0:v:0",
                "-c:v", "libx265", "-preset", "fast", "-crf", "12", "-pix_fmt", "yuv420p10le",
                "-tag:v", "hvc1"]
    ffmpeg.run_capture([ff.binary, "-v", "error", "-y", *args, "-an", "-movflags", "+faststart",
                        str(out)], timeout=1800)
    first = _first_pts(ff, out)
    # with -ss before -i the copy starts at the keyframe; its timestamps restart at 0,
    # so the segment begins `seg.start - keyframe` seconds in
    offset = seg.start - a + max(-first, 0.0) if v.get("codec") != "hevc" else \
        seg.start - _keyframe_before(ff, seg.file, a)
    return out, round(offset, 3)


def _first_pts(ff: FFmpegCfg, path: Path) -> float:
    raw = ffmpeg.run_capture([ff.probe_binary, "-v", "error", "-select_streams", "v:0",
                              "-read_intervals", "%+#1", "-show_entries", "frame=pts_time",
                              "-of", "json", str(path)], timeout=120)
    frames = json.loads(raw or b"{}").get("frames") or [{}]
    return float(frames[0].get("pts_time") or 0.0)


def _keyframe_before(ff: FFmpegCfg, path: str, t: float) -> float:
    """The keyframe a stream-copy seek to `t` starts from."""
    raw = ffmpeg.run_capture([ff.probe_binary, "-v", "error", "-select_streams", "v:0",
                              "-skip_frame", "nokey", "-read_intervals",
                              f"{max(t - 12, 0):.3f}%{t + 0.001:.3f}", "-show_entries",
                              "frame=pts_time", "-of", "json", path], timeout=300)
    keys = [float(f["pts_time"]) for f in json.loads(raw or b"{}").get("frames", [])
            if f.get("pts_time") is not None and float(f["pts_time"]) <= t + 1e-3]
    return max(keys) if keys else 0.0


def render_segment(cli: str, ff: FFmpegCfg, seg: Segment, info: dict[str, Any],
                   workdir: Path, name: str, timeout: float = 7200) -> Segment:
    """Grade one segment through EffectCraft; returns the segment to join in its place."""
    grade = ((seg.fx or {}).get("video") or {}).get("grade") or {}
    piece, offset = cut_piece(ff, seg, info, workdir, name)
    steps = workdir / f"{name}.steps.json"
    steps.write_text(json.dumps(project_commands(piece, info, grade)), encoding="utf-8")
    project = workdir / f"{name}.ecproj"
    _run([cli, "--empty", "--json", "--save-as", str(project), "run", "--commands", str(steps)],
         timeout=600)
    rendered = workdir / f"{name}.graded.mp4"
    length = seg.end - seg.start
    _run([cli, "--project", str(project), "render", "--out", str(rendered), "--format", "hevc",
          "--profile", "main10", "--rate-control", "quality", "--video-quality", "18",
          "--start", f"{offset:.3f}", "--end", f"{offset + length:.3f}", "--audio", "off"],
         timeout=timeout)
    fx = dict(seg.fx or {})
    video = dict(fx.get("video") or {})
    video.pop("grade", None)  # applied by EffectCraft
    if video:
        fx["video"] = video
    else:
        fx.pop("video", None)
    audio = dict(fx.get("audio") or {})
    audio_file = seg.audio_file
    if not audio.get("src"):  # keep the segment's own sound, from the original file
        audio = {**audio, "src": "_source", "start": seg.start}
        audio_file = seg.file
    fx["audio"] = audio
    return replace(seg, file=str(rendered), start=0.0, end=round(length, 3), fx=fx,
                   audio_file=audio_file)


def _run(cmd: list[str], timeout: float) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise EffectCraftError(f"{cmd[0]} not found (Settings → Rendering → EffectCraft)") from exc
    except subprocess.TimeoutExpired as exc:
        raise EffectCraftError(f"EffectCraft timed out after {timeout:.0f}s") from exc
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or proc.stdout).strip().splitlines()[-4:])
        raise EffectCraftError(f"EffectCraft failed ({proc.returncode}): {tail}")

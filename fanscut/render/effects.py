"""Filter graph for timelines with edit operations (timeline/effects.py).

Plain segments never come here: render/plan.py keeps its stream-copy fast
path. Once any segment carries an operation, the whole part goes through one
filter_complex built from numbers only (no text from a recipe reaches the
graph except a title, which ffmpeg reads from a file we write).

Timing model. Segment k lasts d_k = (end - start) / speed + hold in the
output and starts at T_k = sum of earlier d. Video and audio are cut into
pieces whose lengths add up to exactly sum(d), so sync never drifts:

* dissolve into k (duration x): segment k-1's video runs x past its cut,
  taken from the source when it has room (else the last frame is held), and
  xfade overlaps it: the overlap and the extension cancel out. Audio does the
  same with acrossfade.
* J-cut on k (j): k's audio starts j early (from before its in-point) and
  k-1's audio stops j early. L-cut (l): k-1's audio runs l late, k's starts
  l late.
* music bridge: k's audio comes from audio.src at audio.start, for d_k, at
  normal speed; the picture's own audio is dropped.
* every audio piece gets 10 ms fades at hard joins so cuts don't click.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Segment

FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Inter-SemiBold.ttf"
DECLICK = 0.01


@dataclass
class Out:
    """What the encoder needs to know about the graph's output."""
    pix_fmt: str
    hdr: bool


def _q(path: str | Path) -> str:
    """A path as a filter option value: forward slashes, ':' escaped, quoted."""
    p = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


def atempo(speed: float) -> str:
    """atempo chain for any speed (each stage 0.5–2.0)."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1.0) > 1e-6:
        parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def _grade(g: dict[str, float]) -> list[str]:
    out = []
    if "exposure" in g or "contrast" in g or "saturation" in g:
        # exposure in stops: scale brightness-ish via gamma; contrast/saturation direct
        gamma = 2 ** (-g.get("exposure", 0.0) / 2.2)
        out.append(f"eq=contrast={g.get('contrast', 1.0):.4f}:saturation="
                   f"{g.get('saturation', 1.0):.4f}:gamma={1 / gamma:.4f}")
    if g.get("temperature"):
        t = g["temperature"] / 100  # -0.5..0.5: warm (+) shifts red up, blue down
        out.append(f"colorbalance=rm={t:.4f}:bm={-t:.4f}:rh={t / 2:.4f}:bh={-t / 2:.4f}")
    return out


def filter_script(segs: list[Segment], inputs: dict[str, int], first: dict[str, Any],
                  infos: dict[str, dict[str, Any]], audio: bool, workdir: Path,
                  out: Out, audio_map: dict[str, int] | None = None
                  ) -> tuple[str, dict[str, str]]:
    """(filter_complex script, extra work files) for an edited part."""
    w, h = first.get("width") or 1920, first.get("height") or 1080
    fps = round(first.get("fps") or 24, 3) or 24
    n = len(segs)
    fx = [s.fx or {} for s in segs]
    dur = [s.duration for s in segs]
    files: dict[str, str] = {}

    # transitions into k: dissolve overlaps, dip/fade are fades at the join
    xd = [0.0] * n
    for k in range(1, n):
        t = fx[k].get("transition") or {}
        if t.get("type") == "dissolve":
            xd[k] = min(t["duration"], dur[k - 1] / 2, dur[k] / 2)
    # video extension of k-1 for a dissolve into k: from the source if it has room
    vext_src = [0.0] * n
    vext_hold = [0.0] * n
    for k in range(1, n):
        if xd[k]:
            p = segs[k - 1]
            sp = fx[k - 1].get("speed", 1.0)
            room = ((infos[p.file].get("duration") or p.end) - p.end) / sp
            if fx[k - 1].get("hold"):
                room = 0.0  # a held frame continues as a hold
            vext_src[k - 1] = min(xd[k], max(room, 0.0))
            vext_hold[k - 1] = xd[k] - vext_src[k - 1]

    lines: list[str] = []
    for k, s in enumerate(segs):
        f = fx[k]
        sp = f.get("speed", 1.0)
        v = f.get("video") or {}
        src_end = s.end + vext_src[k] * sp
        chain = [f"trim=start={s.start:.3f}:end={src_end:.3f}", "setpts=PTS-STARTPTS"]
        if sp != 1.0:
            chain.append(f"setpts=PTS/{sp:.6f}")
        chain.append(f"fps={fps}")
        if v.get("crop"):
            x, y, cw, ch = v["crop"]
            chain.append(f"crop=iw*{cw:.5f}:ih*{ch:.5f}:iw*{x:.5f}:ih*{y:.5f}")
        if v.get("mirror"):
            chain.append("hflip")
        chain += [f"scale={w}:{h}:force_original_aspect_ratio=decrease",
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2", "setsar=1"]
        chain += _grade(v.get("grade") or {})
        chain.append(f"format={out.pix_fmt}")
        hold = f.get("hold", 0.0) + vext_hold[k]
        if hold:
            chain.append(f"tpad=stop_mode=clone:stop_duration={hold:.3f}")
        fin = v.get("fade_in", 0.0)
        fout = v.get("fade_out", 0.0)
        t_in = f.get("transition") or {}
        if t_in.get("type") in ("dip", "fade"):
            fin = max(fin, t_in["duration"] / 2)
        if k + 1 < n:
            t_next = fx[k + 1].get("transition") or {}
            if t_next.get("type") in ("dip", "fade"):
                fout = max(fout, t_next["duration"] / 2)
        if fin:
            chain.append(f"fade=t=in:st=0:d={min(fin, dur[k]):.3f}")
        if fout:
            d = min(fout, dur[k])
            chain.append(f"fade=t=out:st={dur[k] - d:.3f}:d={d:.3f}")
        if f.get("title"):
            tf = workdir / f"title{k}.txt"
            files[tf.name] = f["title"]["text"]
            td = min(f["title"]["duration"], dur[k])
            ramp = min(0.5, td / 4)
            alpha = (f"if(lt(t\\,{ramp:.3f})\\,t/{ramp:.3f}\\,if(lt(t\\,{td - ramp:.3f})\\,1\\,"
                     f"max(0\\,({td:.3f}-t)/{ramp:.3f})))")
            chain.append(f"drawtext=fontfile={_q(FONT)}:textfile={_q(tf)}:fontcolor=white:"
                         f"fontsize=h*0.045:x=w*0.06:y=h*0.84:alpha='{alpha}':"
                         f"enable='lt(t\\,{td:.3f})':shadowcolor=black@0.6:shadowx=2:shadowy=2")
        lines.append(f"[{inputs[s.file]}:v:0]{','.join(chain)}[v{k}];")

    # join video: xfade where a dissolve asks, else concat
    cur, cur_len = "[v0]", dur[0] + vext_src[0] + vext_hold[0]
    for k in range(1, n):
        nxt = f"[vj{k}]"
        own = dur[k] + vext_src[k] + vext_hold[k]
        if xd[k]:
            off = cur_len - xd[k]
            lines.append(f"{cur}[v{k}]xfade=transition=fade:duration={xd[k]:.3f}:"
                         f"offset={off:.3f}{nxt};")
            cur_len = cur_len - xd[k] + own
        else:
            lines.append(f"{cur}[v{k}]concat=n=2:v=1:a=0{nxt};")
            cur_len += own
        cur = nxt
    lines.append(f"{cur}null[v];" if audio else f"{cur}null[v]")
    if not audio:
        return "\n".join(lines) + "\n", files

    # audio pieces
    lead = [0.0] * n     # extra audio before the in-point (J-cut on k)
    tail = [0.0] * n     # extra audio after the out-point (L-cut on k+1, crossfades)
    cut_head = [0.0] * n
    cut_tail = [0.0] * n
    axf = [0.0] * n      # crossfade into k
    for k in range(1, n):
        j, ll = fx[k].get("j_cut", 0.0), fx[k].get("l_cut", 0.0)
        j = min(j, dur[k - 1] / 2)
        ll = min(ll, dur[k] / 2)
        lead[k] += j
        cut_tail[k - 1] += j
        tail[k - 1] += ll
        cut_head[k] += ll
        axf[k] = max(segs[k].xfade_ms / 1000, xd[k])
        tail[k - 1] += axf[k]  # the crossfade overlap, taken from real content
    for k, s in enumerate(segs):
        f = fx[k]
        a = f.get("audio") or {}
        sp = f.get("speed", 1.0)
        length = dur[k] + lead[k] + tail[k] - cut_head[k] - cut_tail[k]
        if a.get("src") and s.audio_file:
            a0 = a.get("start", 0.0) - lead[k] + cut_head[k]
            src = f"[{inputs[s.audio_file]}:a:{(audio_map or {}).get(s.audio_file, 0)}]"
            chain = [f"atrim=start={max(a0, 0):.3f}:end={max(a0, 0) + length:.3f}",
                     "asetpts=PTS-STARTPTS"]
        else:
            a0 = s.start + (cut_head[k] - lead[k]) * sp
            a1 = s.end + (tail[k] - cut_tail[k]) * sp
            src = f"[{inputs[s.file]}:a:{(audio_map or {}).get(s.file, 0)}]"
            chain = [f"atrim=start={max(a0, 0):.3f}:end={a1:.3f}", "asetpts=PTS-STARTPTS"]
            if sp != 1.0:
                chain.append(atempo(sp))
        chain += ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo",
                  f"apad=whole_dur={length:.3f}", f"atrim=end={length:.3f}"]
        if a.get("volume") is not None:
            chain.append(f"volume={a['volume']:.4f}")
        fi = max(a.get("fade_in", 0.0), DECLICK if not axf[k] else 0.0)
        fo = max(a.get("fade_out", 0.0), DECLICK if (k + 1 >= n or not axf[k + 1]) else 0.0)
        t_in = f.get("transition") or {}
        if t_in.get("type") in ("dip", "fade"):
            fi = max(fi, t_in["duration"] / 2)
        if k + 1 < n and (fx[k + 1].get("transition") or {}).get("type") in ("dip", "fade"):
            fo = max(fo, fx[k + 1]["transition"]["duration"] / 2)
        if fi:
            chain.append(f"afade=t=in:st=0:d={min(fi, length):.3f}")
        if fo:
            chain.append(f"afade=t=out:st={max(length - fo, 0):.3f}:d={min(fo, length):.3f}")
        lines.append(f"{src}{','.join(chain)}[a{k}];")
    cur = "[a0]"
    for k in range(1, n):
        nxt = f"[aj{k}]"
        if axf[k]:
            lines.append(f"{cur}[a{k}]acrossfade=d={axf[k]:.3f}:c1=tri:c2=tri{nxt};")
        else:
            lines.append(f"{cur}[a{k}]concat=n=2:v=0:a=1{nxt};")
        cur = nxt
    lines.append(f"{cur}anull[a]")
    return "\n".join(lines) + "\n", files

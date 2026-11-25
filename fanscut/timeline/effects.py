"""Edit operations on a timeline segment: validation and helpers.

Every field is optional and works only on the user's own footage. Bounds are
tight and nothing here is ever passed to ffmpeg as text: the renderer builds
its own filters from these numbers. No file paths, no raw filters.

    {"audio": {"src": "AUJ", "start": 9120.0, "fade_in": 1.5, "fade_out": 0.5,
               "volume": 0.8},                 # music bridge (or just fades/volume)
     "speed": 0.9,                             # 0.5–2.0, audio keeps pitch (atempo)
     "hold": 0,                                # freeze the last frame N s (0–10)
     "video": {"fade_in": 0, "fade_out": 1.0,  # from/to black
               "crop": [0.05, 0, 0.9, 1],      # x, y, w, h as fractions
               "mirror": false,
               "grade": {"exposure": 0.1, "saturation": 0.95, "temperature": -3,
                         "contrast": 1.0}},
     "transition": {"type": "dissolve", "duration": 0.5},   # into this segment
     "j_cut": 0.4, "l_cut": 0,                 # audio leads / lags the picture cut
     "title": {"text": "Sixty years later", "duration": 3}}
"""

from __future__ import annotations

import math
from typing import Any

from ..errors import FansCutError

TRANSITIONS = ("cut", "dissolve", "dip", "fade")
FX_KEYS = ("audio", "speed", "hold", "video", "transition", "j_cut", "l_cut", "title")
MAX_TITLE_TEXT = 80


class EffectError(FansCutError):
    pass


def _num(v: Any, what: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
        raise EffectError(f"{what}: expected a number")
    if not lo <= float(v) <= hi:
        raise EffectError(f"{what}: must be between {lo:g} and {hi:g}")
    return float(v)


def _obj(v: Any, what: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise EffectError(f"{what}: expected an object")
    extra = set(v) - set(allowed)
    if extra:
        raise EffectError(f"{what}: unknown field(s) {', '.join(sorted(extra))}")
    return v


def validate(seg: dict[str, Any], what: str, sources: dict[str, Any]) -> dict[str, Any]:
    """The cleaned effect fields of one segment ({} when it has none)."""
    out: dict[str, Any] = {}
    if seg.get("audio") is not None:
        a = _obj(seg["audio"], f"{what}.audio", ("src", "start", "fade_in", "fade_out", "volume"))
        clean: dict[str, Any] = {}
        if a.get("src") is not None:
            if a["src"] not in sources:
                raise EffectError(f"{what}.audio.src: unknown source {a['src']!r}")
            clean["src"] = a["src"]
            clean["start"] = _num(a.get("start", 0), f"{what}.audio.start", 0, 86_400)
            dur = sources[a["src"]].get("duration")
            if dur and clean["start"] > dur:
                raise EffectError(f"{what}.audio.start: past the source duration")
        elif a.get("start") is not None:
            raise EffectError(f"{what}.audio.start: only with audio.src (a music bridge)")
        for k in ("fade_in", "fade_out"):
            if a.get(k) is not None:
                clean[k] = _num(a[k], f"{what}.audio.{k}", 0, 30)
        if a.get("volume") is not None:
            clean["volume"] = _num(a["volume"], f"{what}.audio.volume", 0, 2)
        if clean:
            out["audio"] = clean
    if seg.get("speed") is not None:
        sp = _num(seg["speed"], f"{what}.speed", 0.5, 2.0)
        if sp != 1.0:
            out["speed"] = sp
    if seg.get("hold") is not None:
        h = _num(seg["hold"], f"{what}.hold", 0, 10)
        if h:
            out["hold"] = h
    if seg.get("video") is not None:
        v = _obj(seg["video"], f"{what}.video", ("fade_in", "fade_out", "crop", "mirror", "grade"))
        cv: dict[str, Any] = {}
        for k in ("fade_in", "fade_out"):
            if v.get(k):
                cv[k] = _num(v[k], f"{what}.video.{k}", 0, 30)
        if v.get("crop") is not None:
            c = v["crop"]
            if not isinstance(c, list) or len(c) != 4:
                raise EffectError(f"{what}.video.crop: [x, y, w, h] as fractions")
            x, y, w, h = (_num(n, f"{what}.video.crop", 0, 1) for n in c)
            if w < 0.1 or h < 0.1 or x + w > 1.0001 or y + h > 1.0001:
                raise EffectError(f"{what}.video.crop: the box must fit the frame "
                                  "(and keep at least 10 %)")
            if (x, y, w, h) != (0, 0, 1, 1):
                cv["crop"] = [x, y, w, h]
        if v.get("mirror") is not None:
            if not isinstance(v["mirror"], bool):
                raise EffectError(f"{what}.video.mirror: true or false")
            if v["mirror"]:
                cv["mirror"] = True
        if v.get("grade") is not None:
            g = _obj(v["grade"], f"{what}.video.grade",
                     ("exposure", "saturation", "temperature", "contrast"))
            cg: dict[str, float] = {}
            bounds = {"exposure": (-2, 2, 0.0), "saturation": (0, 2, 1.0),
                      "temperature": (-50, 50, 0.0), "contrast": (0.5, 2, 1.0)}
            for k, (lo, hi, neutral) in bounds.items():
                if g.get(k) is not None:
                    val = _num(g[k], f"{what}.video.grade.{k}", lo, hi)
                    if val != neutral:
                        cg[k] = val
            if cg:
                cv["grade"] = cg
        if cv:
            out["video"] = cv
    if seg.get("transition") is not None:
        t = _obj(seg["transition"], f"{what}.transition", ("type", "duration"))
        if t.get("type") not in TRANSITIONS:
            raise EffectError(f"{what}.transition.type: one of {', '.join(TRANSITIONS)}")
        if t["type"] != "cut":
            out["transition"] = {"type": t["type"],
                                 "duration": _num(t.get("duration", 0.5),
                                                  f"{what}.transition.duration", 0.04, 5)}
    for k in ("j_cut", "l_cut"):
        if seg.get(k):
            out[k] = _num(seg[k], f"{what}.{k}", 0, 5)
    if seg.get("title") is not None:
        t = _obj(seg["title"], f"{what}.title", ("text", "duration"))
        text = t.get("text")
        if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TITLE_TEXT
                or any(ord(c) < 32 or 0x7F <= ord(c) < 0xA0 for c in text)):
            raise EffectError(f"{what}.title.text: 1–{MAX_TITLE_TEXT} characters of plain text")
        out["title"] = {"text": text.strip(),
                        "duration": _num(t.get("duration", 3), f"{what}.title.duration", 0.5, 10)}
    if out.get("j_cut") and out.get("l_cut"):
        raise EffectError(f"{what}: a cut is either a J-cut or an L-cut, not both")
    return out


def has_effects(fx: dict[str, Any] | None) -> bool:
    return bool(fx)


def out_duration(start: float, end: float, fx: dict[str, Any] | None) -> float:
    """Seconds this segment lasts in the output."""
    fx = fx or {}
    return (end - start) / fx.get("speed", 1.0) + fx.get("hold", 0.0)

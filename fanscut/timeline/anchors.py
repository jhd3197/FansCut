"""Scene anchors: lossy cut-point fingerprints that survive different releases.

Raw timestamps only work for someone with the author's exact files. An anchor
is a small non-invertible hash of the content around a cut point: 8 s of audio
binarised Haitsma–Kalker style ("a1:"), or 8 s of video as dHashes ("v1:" for
dubs, where the dialogue track differs). On import the anchor is slid across
the user's own file and the cut is re-timed to where it matches.

Nothing here can be turned back into watchable content: 10 audio frames/s at
1 bit per band-difference, 4 video frames/s at 64 bits each.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..config import FFmpegCfg, FingerprintCfg
from ..db import Database
from ..errors import FansCutError
from ..fingerprint.audio import features
from ..render import ffmpeg

WINDOW = 4.0  # seconds each side of the cut point
AUDIO_FPS = 10  # fingerprint/audio.py frame rate
VIDEO_FPS = 4
AUDIO_BER_MAX = 0.32  # accept an audio match under this bit error rate
VIDEO_DIST_MAX = 10.0  # accept a video match under this mean dHash distance
SEARCH = 90.0  # seconds either side of the recipe time to search first


# -- audio anchors -------------------------------------------------------------
def binarize(feats: np.ndarray) -> np.ndarray:
    """(n, 40) features -> (n-2, 39) bits: sign of the band-difference change.

    Adjacent frames are averaged first: without it, codec seek jitter flips
    near-zero differences and the same file matches at ~0.23 BER instead of ~0.18.
    """
    if len(feats) > 2:
        feats = (feats[:-1] + feats[1:]) / 2
    d = feats[:, :-1] - feats[:, 1:]
    return (d[1:] - d[:-1] > 0).astype(np.uint8)


def encode_bits(bits: np.ndarray, prefix: str) -> str:
    return f"{prefix}:" + base64.b64encode(np.packbits(bits).tobytes()).decode()


def decode_bits(s: str, cols: int) -> np.ndarray:
    raw = base64.b64decode(s[3:])
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8))
    bits = bits[:bits.size - bits.size % cols]  # drop packbits' zero padding
    if bits.size == 0:
        raise FansCutError(f"anchor {s[:3]}… decodes to nothing")
    return bits.reshape(-1, cols)


def audio_anchor(feats: np.ndarray) -> str:
    return encode_bits(binarize(feats), "a1")


def match_audio(anchor: np.ndarray, hay: np.ndarray) -> tuple[int, float]:
    """Slide (m,39) anchor bits over (n,39) hay bits -> (best frame, bit error rate)."""
    m = len(anchor)
    if len(hay) < m or m == 0:
        return -1, 1.0
    wins = sliding_window_view(hay, m, axis=0)  # (n-m+1, 39, m)
    bers = (wins != anchor.T[None]).mean(axis=(1, 2))
    best = int(np.argmin(bers))
    return best, float(bers[best])


# -- video anchors ---------------------------------------------------------------
def dhash64(gray: np.ndarray) -> np.uint64:
    """One (8, 9) grayscale frame -> 64-bit difference hash."""
    diff = (gray[:, 1:] > gray[:, :-1]).astype(np.uint8).reshape(64)
    return np.packbits(diff).view(np.uint64)[0]


def video_hashes(frames: np.ndarray) -> np.ndarray:
    """(n, 8, 9) uint8 frames -> (n,) uint64 hashes."""
    return np.array([dhash64(f) for f in frames], dtype=np.uint64)


def _popcount(x: np.ndarray) -> np.ndarray:
    """Bit count per uint64 element, any shape."""
    b = np.ascontiguousarray(x).view(np.uint8).reshape(*x.shape, 8)
    return np.unpackbits(b, axis=-1).sum(axis=-1)


def encode_hashes(hashes: np.ndarray) -> str:
    """(n,) uint64 -> "v1:<base64>"; big-endian so recipes cross machines."""
    return "v1:" + base64.b64encode(hashes.astype(">u8").tobytes()).decode()


def decode_hashes(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s[3:]), dtype=">u8").astype(np.uint64)


def match_video(anchor: np.ndarray, hay: np.ndarray) -> tuple[int, float]:
    """Slide (m,) uint64 hashes over (n,) -> (best frame, mean bit distance)."""
    m = len(anchor)
    if len(hay) < m or m == 0:
        return -1, 64.0
    wins = sliding_window_view(hay, m)  # (n-m+1, m)
    dists = _popcount(np.bitwise_xor(wins, anchor[None, :])).mean(axis=1)
    best = int(np.argmin(dists))
    return best, float(dists[best])


# -- making anchors (author side) ------------------------------------------------
class AnchorCache:
    """SQLite cache: file identity + cut position + kind -> anchor string."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def key(path: str, t: float, kinds: tuple[str, ...]) -> str | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        raw = f"{path}|{st.st_mtime}|{st.st_size}|{t:.2f}|{''.join(sorted(kinds))}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, path: str, t: float, kinds: tuple[str, ...]) -> dict[str, str] | None:
        k = self.key(path, t, kinds)
        row = self.db.one("SELECT value FROM anchor_cache WHERE key = ?", (k,)) if k else None
        if row is None:
            return None
        return {v.split(":", 1)[0]: v for v in row["value"].split("\n")}

    def put(self, path: str, t: float, anchors: dict[str, str]) -> None:
        kinds = tuple(anchors)
        k = self.key(path, t, kinds)
        if k:
            self.db.execute("INSERT OR REPLACE INTO anchor_cache (key, value) VALUES (?,?)",
                            (k, "\n".join(anchors[k2] for k2 in sorted(kinds))))


def _audio_at(ff: FFmpegCfg, path: str, t: float, fp: FingerprintCfg) -> np.ndarray:
    # decode a little past the window: features() loses ~0.2 s to its FFT padding,
    # and the anchor must cover the cut point symmetrically
    start = max(t - WINDOW, 0.0)
    pcm = ffmpeg.decode_audio(ff, path, start, 2 * WINDOW + 0.35, fp.sample_rate)
    return features(pcm, fp.sample_rate)[:int(2 * WINDOW * AUDIO_FPS)]


def make_anchors(ff: FFmpegCfg, path: str, t: float, fp: FingerprintCfg,
                 kinds: tuple[str, ...] = ("a", "v"),
                 cache: AnchorCache | None = None) -> dict[str, str]:
    """Anchors for a cut at t seconds in the author's file."""
    if cache:
        hit = cache.get(path, t, kinds)
        if hit and all(k in hit for k in kinds):
            return hit
    out: dict[str, str] = {}
    if "a" in kinds:
        out["a"] = audio_anchor(_audio_at(ff, path, t, fp))
    if "v" in kinds:
        start = max(t - WINDOW, 0.0)
        crop = ffmpeg.detect_crop(ff, path, start, 2 * WINDOW)
        frames = ffmpeg.decode_video(ff, path, start, 2 * WINDOW, VIDEO_FPS, crop)
        out["v"] = encode_hashes(video_hashes(frames))
    if cache:
        cache.put(path, t, out)
    return out


# -- matching on import (user side) ------------------------------------------------
def _video_at(ff: FFmpegCfg, path: str, start: float, duration: float) -> np.ndarray:
    crop = ffmpeg.detect_crop(ff, path, start, min(duration, 60.0))
    return ffmpeg.decode_video(ff, path, start, duration, VIDEO_FPS, crop)


def find_in_file(ff: FFmpegCfg, fp: FingerprintCfg, path: str, t: float,
                 anchors: dict[str, str], search: float = SEARCH,
                 widen: bool = True) -> dict[str, Any]:
    """Locate the recipe's cut time t in the user's file.

    Returns {time, offset, method, confidence}. method is audio | video | none.
    """
    audio_bits = decode_bits(anchors["a"], 39) if "a" in anchors else None
    video_bits = decode_hashes(anchors["v"]) if "v" in anchors else None

    def try_window(start: float, duration: float) -> dict[str, Any] | None:
        if audio_bits is not None:
            pcm = ffmpeg.decode_audio(ff, path, start, duration, fp.sample_rate)
            pos, ber = match_audio(audio_bits, binarize(features(pcm, fp.sample_rate)))
            if pos >= 0 and ber <= AUDIO_BER_MAX:
                # the anchor's centre row sits at the cut time; bit rows are 0.1 s
                # (calibrated on fixtures: +2 rows lands the cut within one frame)
                hit = start + (pos + len(audio_bits) / 2 + 2) / AUDIO_FPS
                return {"time": round(hit, 2), "offset": round(hit - t, 2),
                        "method": "audio", "confidence": round(1 - ber / AUDIO_BER_MAX, 3)}
        if video_bits is not None:
            frames = _video_at(ff, path, start, duration)
            pos, dist = match_video(video_bits, video_hashes(frames))
            if pos >= 0 and dist <= VIDEO_DIST_MAX:
                hit = start + (pos + len(video_bits) / 2 + 0.5) / VIDEO_FPS
                return {"time": round(hit, 2), "offset": round(hit - t, 2),
                        "method": "video", "confidence": round(1 - dist / VIDEO_DIST_MAX, 3)}
        return None

    start = max(t - search - WINDOW, 0.0)
    duration = 2 * (search + WINDOW)
    found = try_window(start, duration)
    if found is None and widen:
        try:
            total = float(ffmpeg.probe(ff, path).get("duration") or 0.0)
        except FansCutError:
            total = 0.0
        if total > duration:
            found = try_window(0.0, total)
    if found is None:
        return {"time": t, "offset": None, "method": "none", "confidence": 0.0}
    return found


# -- reconciling one episode's boundaries --------------------------------------------
DRIFT_MIN = 0.5 / 60  # 0.5 s of drift per minute of runtime = a speedup
OUTLIER_S = 1.0


def reconcile(boundaries: list[dict[str, Any]]) -> dict[str, Any]:
    """One episode's boundary matches -> corrected times with a strategy.

    boundaries: [{"t": recipe time, "offset": float|None, "confidence": 0..1}, ...]
    Most releases have one constant offset (median wins, outliers flagged). Steady
    drift gets a linear fit (PAL speedup). A mid-episode jump (ads cut out) falls
    back to per-boundary offsets. Unmatched boundaries take the episode model.
    """
    matched = [(b["t"], b["offset"]) for b in boundaries if b.get("offset") is not None]
    strategy = "raw"
    model: Any = None
    median = 0.0
    if matched:
        ts = np.array([m[0] for m in matched])
        offs = np.array([m[1] for m in matched])
        median = float(np.median(offs))
        strategy, model = "constant", (lambda t, m=median: m)
        if len(matched) >= 3:
            a, b = np.polyfit(ts, offs, 1)
            if abs(a) > DRIFT_MIN and np.abs(offs - (a * ts + b)).max() < 2 * OUTLIER_S:
                strategy, model = "linear", (lambda t, a=a, b=b: a * t + b)
        if strategy == "constant":
            inliers = int((np.abs(offs - median) <= OUTLIER_S).sum())
            if inliers * 2 < len(matched):
                # no majority cluster: the release has a mid-episode jump
                strategy = "per-boundary"
    out = []
    for bnd in boundaries:
        t, off = bnd["t"], bnd.get("offset")
        if strategy == "per-boundary":
            corrected = t + (off if off is not None else median)
            outlier = False
            own = off is not None
        elif model is not None:
            model_off = model(t)
            own = off is not None and abs(off - model_off) <= OUTLIER_S
            corrected = t + (off if own else model_off)
            outlier = bool(off is not None and not own)
        else:
            corrected, own, outlier = t, False, False
        out.append({**bnd, "offset": off if off is not None else None,
                    "corrected": round(float(corrected), 2),
                    "outlier": outlier,
                    "low_confidence": not own or bnd.get("confidence", 0) < 0.3})
    return {"strategy": strategy, "boundaries": out}

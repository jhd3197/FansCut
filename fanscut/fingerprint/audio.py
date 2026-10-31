"""Audio -> frame features. Pure numpy; decoding lives in render.ffmpeg.

A frame is the log energy in log-spaced bands, minus its own mean (so volume
changes do not matter), z-scored per band over the clip (so a shared spectral
tilt does not make unrelated music look alike), then L2-normalised. Cosine
similarity of two frames is then a dot product.
"""

from __future__ import annotations

import numpy as np

FRAME_RATE = 10  # feature frames per second
BANDS = 40
SILENCE_RMS = 1e-4


def frame_hop(sample_rate: int) -> int:
    return sample_rate // FRAME_RATE


def features(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """(frames, BANDS) float32 unit vectors; silent frames are all zeros."""
    hop = frame_hop(sample_rate)
    n_fft = 1 << int(np.ceil(np.log2(hop * 1.3)))
    if len(samples) < n_fft:
        return np.zeros((0, BANDS), dtype=np.float32)
    n = 1 + (len(samples) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    frames = samples[idx].astype(np.float32)
    rms = np.sqrt((frames**2).mean(axis=1))
    spec = np.abs(np.fft.rfft(frames * np.hanning(n_fft).astype(np.float32), axis=1)) ** 2
    freqs = np.fft.rfftfreq(n_fft, 1 / sample_rate)
    edges = np.geomspace(80, 0.45 * sample_rate, BANDS + 1)
    band = np.zeros((n, BANDS), dtype=np.float32)
    for b in range(BANDS):
        sel = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if sel.any():
            band[:, b] = spec[:, sel].sum(axis=1)
        else:  # narrow low bands can fall between bins: take the nearest one
            band[:, b] = spec[:, np.argmin(np.abs(freqs - edges[b]))]
    logb = np.log(band + 1e-10)
    logb -= logb.mean(axis=1, keepdims=True)
    voiced = rms >= SILENCE_RMS
    if voiced.sum() > 1:
        mu = logb[voiced].mean(axis=0)
        sd = logb[voiced].std(axis=0) + 1e-6
        logb = (logb - mu) / sd
    norm = np.linalg.norm(logb, axis=1, keepdims=True)
    out = logb / np.maximum(norm, 1e-6)
    out[~voiced] = 0.0
    return out.astype(np.float32)

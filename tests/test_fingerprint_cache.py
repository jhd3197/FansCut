"""Fingerprint cache: roundtrip, invalidation, and reuse in analyze_files."""

import os

import numpy as np

from fanscut.config import FFmpegCfg, FingerprintCfg
from fanscut.fingerprint import detect
from fanscut.fingerprint.audio import BANDS
from fanscut.fingerprint.cache import FingerprintCache, cache_key


def _clips(rows_h=10, rows_t=8, tail_start=240.0, duration=300.0):
    rng = np.random.default_rng(1)
    head = rng.random((rows_h, BANDS), dtype=np.float32)
    tail = rng.random((rows_t, BANDS), dtype=np.float32)
    return detect.EpisodeClips(head, tail, tail_start, duration)


def test_roundtrip(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"video")
    fp = FingerprintCfg()
    cache = FingerprintCache(db)
    assert cache.get(str(f), fp) is None
    clip = _clips()
    cache.put(str(f), clip, fp)
    got = cache.get(str(f), fp)
    assert got is not None
    assert got.head.shape == clip.head.shape and np.allclose(got.head, clip.head)
    assert got.tail.shape == clip.tail.shape and np.allclose(got.tail, clip.tail)
    assert got.tail_start == 240.0 and got.duration == 300.0


def test_key_invalidates_on_file_or_settings_change(db, tmp_path):
    f = tmp_path / "ep.mkv"
    f.write_bytes(b"video")
    fp = FingerprintCfg()
    cache = FingerprintCache(db)
    cache.put(str(f), _clips(), fp)
    assert cache.get(str(f), fp) is not None
    assert cache.get(str(f), FingerprintCfg(sample_rate=16000)) is None  # settings changed
    f.write_bytes(b"longer video")  # size changed
    assert cache.get(str(f), fp) is None
    assert cache_key(str(tmp_path / "missing.mkv"), fp) is None


def test_analyze_files_uses_cache_on_second_run(db, tmp_path, monkeypatch):
    files = []
    for n in (1, 2, 3):
        p = tmp_path / f"e{n}.mkv"
        p.write_bytes(f"episode {n}".encode())
        files.append((f"S01E0{n}", str(p), 300.0))
    calls = []

    def fake_decode(_ff, path, start, duration, rate):
        calls.append(path)
        rng = np.random.default_rng(abs(hash(path)) % 2**32)
        return rng.random(int(rate * duration), dtype=np.float32) * 0.01

    monkeypatch.setattr("fanscut.fingerprint.detect.ffmpeg.decode_audio", fake_decode)
    fp = FingerprintCfg(sample_rate=8000, head_minutes=0.02, tail_minutes=0.02)
    cache = FingerprintCache(db)
    first = detect.analyze_files(files, fp, FFmpegCfg(), cache=cache)
    assert len(calls) == 6  # head + tail per episode
    second = detect.analyze_files(files, fp, FFmpegCfg(), cache=cache)
    assert len(calls) == 6  # nothing decoded again
    assert second == first
    # a changed file is re-decoded, the rest stay cached
    os.utime(files[1][1], (0, 0))
    detect.analyze_files(files, fp, FFmpegCfg(), cache=cache)
    assert len(calls) == 8

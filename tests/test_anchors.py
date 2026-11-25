"""Anchor encode/decode, matching and reconciliation — pure numpy, no ffmpeg."""

import numpy as np

from fanscut.timeline import anchors


def feats(rng, frames=200, bands=40):
    return rng.random((frames, bands), dtype=np.float32)


def test_binarize_shape_and_bits():
    bits = anchors.binarize(feats(np.random.default_rng(1)))
    assert bits.shape == (198, 39) and set(np.unique(bits)) <= {0, 1}


def test_audio_anchor_roundtrip():
    s = anchors.audio_anchor(feats(np.random.default_rng(2), frames=80))
    assert s.startswith("a1:")
    bits = anchors.decode_bits(s, 39)
    assert bits.shape == (78, 39)
    assert 380 <= len(s) - 3 <= 540  # ~390 bytes of base64, as specced


def test_video_anchor_roundtrip_big_endian():
    h = np.array([0x0123456789ABCDEF, 42, 2**64 - 1], dtype=np.uint64)
    s = anchors.encode_hashes(h)
    assert s.startswith("v1:") and len(s) == 3 + 32  # 3*8 bytes -> 32 base64 chars
    assert np.array_equal(anchors.decode_hashes(s), h)


def test_dhash64_is_64_bit():
    gray = np.arange(72, dtype=np.uint8).reshape(8, 9)
    assert anchors.dhash64(gray) == 2**64 - 1  # strictly increasing: every ">" is True
    rng = np.random.default_rng(3)
    h = anchors.dhash64(rng.integers(0, 255, (8, 9), dtype=np.uint8))
    assert 0 <= int(h) < 2**64


def test_match_audio_finds_shift():
    rng = np.random.default_rng(4)
    hay = anchors.binarize(feats(rng, frames=600))
    needle = hay[300:379]
    pos, ber = anchors.match_audio(needle, hay)
    assert pos == 300 and ber == 0.0
    noisy = needle.copy()
    noisy[rng.random(needle.shape) < 0.1] ^= 1  # 10% bit errors
    pos, ber = anchors.match_audio(noisy, hay)
    assert pos == 300 and ber <= anchors.AUDIO_BER_MAX
    pos, ber = anchors.match_audio(anchors.binarize(feats(np.random.default_rng(9))), hay)
    assert ber > anchors.AUDIO_BER_MAX  # unrelated audio does not pass


def test_match_video_finds_shift():
    rng = np.random.default_rng(5)
    hay = rng.integers(0, 2**64, 100, dtype=np.uint64)
    needle = hay[40:72]
    pos, dist = anchors.match_video(needle, hay)
    assert pos == 40 and dist == 0.0
    pos, dist = anchors.match_video(rng.integers(0, 2**64, 32, dtype=np.uint64), hay)
    assert dist > anchors.VIDEO_DIST_MAX


def _b(t, off, conf=0.9):
    return {"t": float(t), "offset": off, "confidence": conf}


def test_reconcile_constant_offset():
    b = [_b(100, 2.3), _b(200, 2.3), _b(300, 2.4)]
    r = anchors.reconcile(b)
    assert r["strategy"] == "constant"
    assert [x["corrected"] for x in r["boundaries"]] == [102.3, 202.3, 302.4]
    assert not any(x["outlier"] for x in r["boundaries"])


def test_reconcile_outlier_uses_median():
    b = [_b(100, 2.3), _b(200, 2.3), _b(300, 9.0)]  # third is wrong
    r = anchors.reconcile(b)
    assert r["boundaries"][2]["outlier"] is True
    assert r["boundaries"][2]["corrected"] == 302.3  # median, not the bad match


def test_reconcile_linear_drift():
    b = [_b(t, 1.0 - 0.041 * t) for t in (0, 300, 600, 900, 1200)]  # PAL speedup
    r = anchors.reconcile(b)
    assert r["strategy"] == "linear"
    assert r["boundaries"][4]["corrected"] == 1200 + (1.0 - 0.041 * 1200)


def test_reconcile_jump_goes_per_boundary():
    b = [_b(20, 0.0), _b(40, 0.0), _b(61.2, None), _b(90, -30.0), _b(110, -30.0)]
    r = anchors.reconcile(b)
    assert r["strategy"] == "per-boundary"
    corr = [x["corrected"] for x in r["boundaries"]]
    assert corr[:2] == [20.0, 40.0] and corr[3:] == [60.0, 80.0]
    assert r["boundaries"][2]["low_confidence"] is True  # unmatched: median fallback


def test_reconcile_no_matches_is_raw_and_flagged():
    r = anchors.reconcile([_b(50, None), _b(80, None)])
    assert r["strategy"] == "raw"
    assert [x["corrected"] for x in r["boundaries"]] == [50.0, 80.0]
    assert all(x["low_confidence"] for x in r["boundaries"])

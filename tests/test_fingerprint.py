"""Fingerprint core on synthetic audio: noise episodes sharing an inserted theme."""

import numpy as np

from fanscut.config import FingerprintCfg
from fanscut.fingerprint.audio import features
from fanscut.fingerprint.detect import EpisodeClips, detect
from fanscut.fingerprint.match import find_shared

SR = 8000
FP = FingerprintCfg(head_minutes=2, tail_minutes=2)


def noise(rng, seconds):
    return rng.normal(0, 0.1, int(seconds * SR)).astype(np.float32)


def theme(seed, seconds):
    return noise(np.random.default_rng(seed), seconds)


def make_episode(rng, intro_at, outro_len_before_end, intro, outro, *, total=300.0):
    """Noise episode: `intro` at intro_at s, `outro` ending that far before the end."""
    parts = [noise(rng, intro_at), intro]
    story_end = total - outro_len_before_end - len(outro) / SR
    cur = sum(len(p) for p in parts) / SR
    parts.append(noise(rng, story_end - cur))
    parts.append(outro)
    parts.append(noise(rng, max(total - sum(len(p) for p in parts) / SR, 1)))
    return np.concatenate(parts)


def clips_of(audio_list):
    out = []
    for audio in audio_list:
        dur = len(audio) / SR
        head = audio[: int(120 * SR)]
        tail_start = max(dur - 120, 0)
        out.append(EpisodeClips(features(head, SR), features(audio[int(tail_start * SR):], SR),
                                tail_start, dur))
    return out


def test_find_shared_reports_offsets_in_both_clips():
    rng = np.random.default_rng(1)
    shared = theme(99, 20)
    a = np.concatenate([noise(rng, 10), shared, noise(rng, 15)])
    b = np.concatenate([noise(rng, 33), shared, noise(rng, 5)])
    m = find_shared(features(a, SR), features(b, SR), 0.75, 15, 180)
    assert m
    assert abs(m[0].a_start - 10) < 0.5 and abs(m[0].b_start - 33) < 0.5
    assert abs(m[0].length - 20) < 0.5


def test_unrelated_audio_has_no_match():
    rng = np.random.default_rng(2)
    a, b = features(noise(rng, 60), SR), features(noise(rng, 60), SR)
    assert not find_shared(a, b, 0.75, 15, 180)


def test_volume_change_still_matches():
    rng = np.random.default_rng(3)
    shared = theme(7, 20)
    a = np.concatenate([noise(rng, 5), shared, noise(rng, 5)])
    b = np.concatenate([noise(rng, 8), shared * 0.4, noise(rng, 5)])
    assert find_shared(features(a, SR), features(b, SR), 0.75, 15, 180)


def test_detect_intro_and_outro_at_varying_offsets():
    rng = np.random.default_rng(4)
    op, ed = theme(10, 30), theme(11, 25)
    intro_at = [20, 45, 8, 70]
    eps = [make_episode(rng, t, 5 + 3 * k, op, ed) for k, t in enumerate(intro_at)]
    res = detect(clips_of(eps), FP)
    for k, t in enumerate(intro_at):
        s, e = res[k]["intro"]
        assert abs(s - t) < 1.0 and abs(e - (t + 30)) < 1.0
        os_, oe = res[k]["outro"]
        assert abs(os_ - (300 - (5 + 3 * k) - 25)) < 1.0
        assert abs((oe - os_) - 25) < 1.0


def test_detect_recap_and_preview():
    rng = np.random.default_rng(5)
    op, ed = theme(20, 30), theme(21, 25)
    # the last scene of episode i is replayed as the recap of episode i+1
    scenes = [theme(30 + i, 25) for i in range(3)]
    eps = []
    for i in range(4):
        recap = scenes[i - 1] if i > 0 else None
        story_tail = scenes[i] if i < 3 else theme(99, 25)
        head = [recap] if recap is not None else []
        parts = [*head, noise(rng, 20), op, noise(rng, 150), story_tail, ed, noise(rng, 10)]
        eps.append(np.concatenate(parts))
    res = detect(clips_of(eps), FP)
    assert "recap" not in res[0]
    for i in (1, 2):
        s, e = res[i]["recap"]
        assert s <= 1.0 and abs(e - 25) < 1.5
        assert e <= res[i]["intro"][0] + 0.5


def test_single_episode_finds_nothing():
    rng = np.random.default_rng(6)
    assert detect(clips_of([noise(rng, 100)]), FP) == [{}]

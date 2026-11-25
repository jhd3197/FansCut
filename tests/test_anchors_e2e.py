"""Anchors vs six synthetic 'other releases' of the same episode (real ffmpeg)."""

import json
import subprocess
from pathlib import Path

import pytest

from fanscut.config import FFmpegCfg, FingerprintCfg
from fanscut.timeline import anchors
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg

FF = FFmpegCfg()
FP = FingerprintCfg()
DUR = 120.0
CUTS = [30.5, 61.2, 100.0]
# a moving chirp over a music bed + noise: every 8 s window sounds different
BASE_AUDIO = ("aevalsrc=0.2*sin(6.283*220*t)+0.25*sin(6.283*(300+500*t/120)*t)"
              "+0.15*random(0):s=8000:duration=120")
# a real dub shares no waveform with the original: pure noise forces the video path
DUB_AUDIO = "anoisesrc=color=white:seed=9:amplitude=0.5:duration=122"


def _run(args, out: Path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True)


def _probe_dur(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(r.stdout)["format"]["duration"])


@pytest.fixture(scope="module")
def releases(tmp_path_factory):
    """The author's file plus six ways someone else's copy can differ."""
    d = tmp_path_factory.mktemp("releases")
    base = d / "base.mkv"
    _run(["-f", "lavfi", "-i", "mandelbrot=size=320x240:rate=24", "-t", "120",
          "-f", "lavfi", "-i", BASE_AUDIO, "-c:v", "libx264", "-g", "24", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest"], base)
    out = {"base": base}

    shifted = d / "shifted.mkv"  # +2.3 s bumper
    _run(["-i", str(base), "-vf", "tpad=start_duration=2.3", "-af", "adelay=2300",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"], shifted)
    out["shifted"] = shifted

    reenc = d / "reenc.mkv"  # different codec and bitrate
    _run(["-i", str(base), "-c:v", "mpeg4", "-q:v", "9", "-c:a", "aac", "-b:a", "64k"], reenc)
    out["reenc"] = reenc

    tempo = d / "tempo.mkv"  # PAL speedup: 25 fps, tempo x1.0427
    _run(["-i", str(base), "-vf", "setpts=PTS/1.0427,fps=25", "-af", "atempo=1.0427",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"], tempo)
    out["tempo"] = tempo

    block = d / "blockcut.mkv"  # 30 s removed from the middle (ads)
    _run(["-i", str(base), "-filter_complex",
          "[0:v]trim=0:50,setpts=PTS-STARTPTS[v0];[0:a]atrim=0:50,asetpts=PTS-STARTPTS[a0];"
          "[0:v]trim=80:120,setpts=PTS-STARTPTS[v1];[0:a]atrim=80:120,asetpts=PTS-STARTPTS[a1];"
          "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p"], block)
    out["blockcut"] = block

    letter = d / "letterbox.mkv"  # black bars
    _run(["-i", str(base), "-vf", "pad=320:290:0:25:black",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy"], letter)
    out["letterbox"] = letter

    dub = d / "dub.mkv"  # different voice track, video shifted 1.7 s
    _run(["-i", str(base), "-f", "lavfi", "-i", DUB_AUDIO,
          "-filter_complex", "[0:v]tpad=start_duration=1.7[v];[1:a]adelay=1700[a]",
          "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-shortest"], dub)
    out["dub"] = dub
    return out


@pytest.fixture(scope="module")
def author_anchors(releases):
    return {t: anchors.make_anchors(FF, str(releases["base"]), t, FP) for t in CUTS}


def _corrected(release, t, anchor):
    r = anchors.find_in_file(FF, FP, str(release), t, anchor)
    assert r["method"] != "none", f"no match at t={t} in {release}"
    return r


def test_same_release_is_exact(releases, author_anchors):
    for t, a in author_anchors.items():
        r = _corrected(releases["base"], t, a)
        assert abs(r["time"] - t) <= 0.1 and r["method"] == "audio"


def test_bumper_shift_corrects(releases, author_anchors):
    for t, a in author_anchors.items():
        r = _corrected(releases["shifted"], t, a)
        assert abs(r["time"] - (t + 2.3)) <= 0.25  # frame grid + codec priming near file end


def test_reencode_survives(releases, author_anchors):
    for t, a in author_anchors.items():
        r = _corrected(releases["reenc"], t, a)
        assert abs(r["time"] - t) <= 0.25  # codec shift + frame grid


def test_tempo_speedup_lands_close(releases, author_anchors):
    for t, a in author_anchors.items():
        r = _corrected(releases["tempo"], t, a)
        assert abs(r["time"] - t / 1.0427) <= 0.3  # resolves through video at 4 fps
    # and reconcile recognises the drift as a speedup
    pts = [{"t": t, "offset": _corrected(releases["tempo"], t, a)["offset"], "confidence": 0.9}
           for t, a in author_anchors.items()]
    assert anchors.reconcile(pts)["strategy"] == "linear"


def test_blockcut_resolves_after_the_gap(releases, author_anchors):
    r = _corrected(releases["blockcut"], 100.0, author_anchors[100.0])
    assert abs(r["time"] - 70.0) <= 0.2
    r = _corrected(releases["blockcut"], 30.5, author_anchors[30.5])
    assert abs(r["time"] - 30.5) <= 0.15
    gone = anchors.find_in_file(FF, FP, str(releases["blockcut"]), 61.2, author_anchors[61.2],
                                widen=True)
    assert gone["method"] == "none"  # content is gone: no false match


def test_letterbox_matches_via_crop(releases, author_anchors):
    v_only = {t: {"v": a["v"]} for t, a in author_anchors.items()}
    for t, a in v_only.items():
        r = _corrected(releases["letterbox"], t, a)
        assert abs(r["time"] - t) <= 0.3 and r["method"] == "video"


def test_dub_resolves_through_video(releases, author_anchors):
    for t, a in author_anchors.items():
        r = _corrected(releases["dub"], t, a)
        assert abs(r["time"] - (t + 1.7)) <= 0.3
        assert r["method"] == "video"


def test_reconcile_blockcut_per_boundary(releases, author_anchors):
    pts = []
    for t, a in author_anchors.items():
        r = anchors.find_in_file(FF, FP, str(releases["blockcut"]), t, a)
        pts.append({"t": t, "offset": r["offset"], "confidence": r["confidence"]})
    rec = anchors.reconcile(pts)
    by_t = {b["t"]: b for b in rec["boundaries"]}
    assert abs(by_t[100.0]["corrected"] - 70.0) <= 0.3
    assert abs(by_t[30.5]["corrected"] - 30.5) <= 0.3
    assert by_t[61.2]["low_confidence"] is True

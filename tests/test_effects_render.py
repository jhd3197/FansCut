"""Edit operations rendered for real: one fixture per operation.

Each test checks the output duration, that audio has no clicks at the cuts
(sample-to-sample jumps stay small on sine sources), and, for subtitles, cue
timing within ±0.1 s.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from fanscut.config import FFmpegCfg
from fanscut.models import Segment
from fanscut.planner import CutOptions, CutPlan
from fanscut.render import ffmpeg, plan, run, subs
from fanscut.timeline import effects
from tests.conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
FF = FFmpegCfg(preset="ultrafast", crf=30)


def src(path: Path, seconds: float = 8, freq: int = 440, srt: str | None = None,
        size: str = "160x120") -> str:
    """testsrc video + sine audio (+ an optional English subtitle track)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc=duration={seconds}:size={size}:rate=24", "-f", "lavfi", "-i",
           f"sine=frequency={freq}:duration={seconds}:sample_rate=48000"]
    maps = ["-map", "0:v", "-map", "1:a"]
    if srt:
        s = path.with_suffix(".srt")
        s.write_text(srt, encoding="utf-8")
        cmd += ["-i", str(s)]
        maps += ["-map", "2:s", "-metadata:s:s:0", "language=eng"]
    subprocess.run([*cmd, *maps, "-c:v", "libx264", "-g", "24", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-c:s", "srt", "-shortest", str(path)],
                   check=True)
    return str(path)


def render(tmp: Path, segs: list[Segment], name: str = "out.mkv") -> Path:
    cut = CutPlan(segs, [], [], 0, sum(s.duration for s in segs), CutOptions())
    out = tmp / name
    work = tmp / f"work-{name}"
    work.mkdir()
    rp = plan.build_plan(cut, FF, out, work, lambda f: ffmpeg.probe(FF, f))
    run.execute(rp, work, lambda _p: None)
    return out


def seg(file: str, a: float, b: float, **fx) -> Segment:
    return Segment("S", file, a, b, fx=fx, audio_file=fx.pop("_audio_file", None))


def duration(p: Path) -> float:
    return float(ffmpeg.probe(FF, str(p))["duration"])


def pcm(p: Path) -> np.ndarray:
    return ffmpeg.decode_audio(FF, str(p), 0, 60, 48000)


def max_jump(a: np.ndarray) -> float:
    return float(np.max(np.abs(np.diff(a)))) if len(a) > 1 else 0.0


def peak_freq(a: np.ndarray, sr: int = 48000) -> float:
    spec = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return float(np.fft.rfftfreq(len(a), 1 / sr)[np.argmax(spec)])


@pytest.fixture
def media(tmp_path):
    return {"a": src(tmp_path / "a.mkv"), "b": src(tmp_path / "b.mkv", freq=660)}


def test_plain_segments_keep_the_fast_path(tmp_path, media):
    cut = CutPlan([Segment("S", media["a"], 1, 3), Segment("S", media["a"], 4, 6)],
                  [], [], 0, 4, CutOptions())
    rp = plan.build_plan(cut, FF, tmp_path / "o.mkv", tmp_path, lambda f: ffmpeg.probe(FF, f))
    assert rp.mode == "copy"


@pytest.mark.parametrize("speed", [0.5, 2.0])
def test_speed_keeps_pitch(tmp_path, media, speed):
    out = render(tmp_path, [seg(media["a"], 1, 5, speed=speed)])
    assert duration(out) == pytest.approx(4 / speed, abs=0.1)
    assert peak_freq(pcm(out)) == pytest.approx(440, abs=15)  # atempo: same pitch


def test_hold_freezes_the_last_frame(tmp_path, media):
    out = render(tmp_path, [seg(media["a"], 1, 3, hold=1.5)])
    assert duration(out) == pytest.approx(3.5, abs=0.1)


def test_dissolve_keeps_total_length_and_no_clicks(tmp_path, media):
    segs = [seg(media["a"], 1, 4), seg(media["b"], 2, 5, transition={"type": "dissolve",
                                                                     "duration": 0.5})]
    out = render(tmp_path, segs)
    assert duration(out) == pytest.approx(6.0, abs=0.1)
    assert max_jump(pcm(out)) < 0.12


def test_dip_to_black_fades_the_join(tmp_path, media):
    segs = [seg(media["a"], 1, 4), seg(media["b"], 2, 5, transition={"type": "dip",
                                                                     "duration": 1.0})]
    out = render(tmp_path, segs)
    assert duration(out) == pytest.approx(6.0, abs=0.1)
    frame = ffmpeg.decode_video(FF, str(out), 2.98, 0.1, fps=24)
    assert frame.mean() < 25  # black at the join


def test_hard_cut_between_phases_does_not_click(tmp_path, media):
    # 440 Hz and 660 Hz cut at arbitrary points: a raw join would jump
    segs = [seg(media["a"], 1.013, 3.0, video={"mirror": True}),
            seg(media["b"], 2.371, 4.0, video={"mirror": True})]
    out = render(tmp_path, segs)
    assert duration(out) == pytest.approx(3.616, abs=0.1)
    assert max_jump(pcm(out)) < 0.12


@pytest.mark.parametrize("kind", ["j_cut", "l_cut"])
def test_j_and_l_cuts_keep_sync_length(tmp_path, media, kind):
    segs = [seg(media["a"], 1, 4), seg(media["b"], 2, 5, **{kind: 0.6})]
    out = render(tmp_path, segs)
    assert duration(out) == pytest.approx(6.0, abs=0.1)
    a = pcm(out)
    # J: the 660 Hz audio is already there 0.6 s before the picture cut at 3.0 s
    # L: the 440 Hz audio still plays 0.6 s after it
    window = a[int(2.55 * 48000):int(2.85 * 48000)] if kind == "j_cut" \
        else a[int(3.15 * 48000):int(3.45 * 48000)]
    assert peak_freq(window) == pytest.approx(660 if kind == "j_cut" else 440, abs=15)
    assert max_jump(a) < 0.12


def test_music_bridge_replaces_the_audio(tmp_path, media):
    s = seg(media["a"], 1, 4, audio={"src": "B", "start": 2.0, "fade_in": 0.3, "volume": 0.8},
            _audio_file=media["b"])
    out = render(tmp_path, [s])
    assert duration(out) == pytest.approx(3.0, abs=0.1)
    assert peak_freq(pcm(out)[48000:96000]) == pytest.approx(660, abs=15)


def test_crop_and_grade(tmp_path, media):
    out = render(tmp_path, [seg(media["a"], 1, 3, video={
        "crop": [0.25, 0.25, 0.5, 0.5], "grade": {"saturation": 0.0, "exposure": 0.3}})])
    assert duration(out) == pytest.approx(2.0, abs=0.1)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
                         capture_output=True, check=True).stdout
    u = np.frombuffer(raw, np.uint8)[160 * 120:2 * 160 * 120]
    assert abs(float(u.mean()) - 128) < 4  # saturation 0: no colour left


def test_title_card(tmp_path):
    big = src(tmp_path / "big.mkv", size="640x360")
    plain = render(tmp_path, [seg(big, 1, 4)], "plain.mkv")
    titled = render(tmp_path, [seg(big, 1, 4, title={"text": "Sixty years later",
                                                            "duration": 2})], "titled.mkv")
    assert duration(titled) == pytest.approx(3.0, abs=0.1)
    def gray(p: Path, t: float) -> np.ndarray:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(p),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(360, 640).astype(int)

    band = (slice(295, 335), slice(20, 400))  # where the caption sits
    shown = np.abs(gray(titled, 1.0)[band] - gray(plain, 1.0)[band])
    gone = np.abs(gray(titled, 2.6)[band] - gray(plain, 2.6)[band])
    assert (shown > 60).sum() > 20  # white glyphs over the picture
    assert (gone > 60).sum() < 5  # after its 2 s the card is gone


def test_subtitles_follow_the_cut(tmp_path):
    srt = ("1\n00:00:01,000 --> 00:00:02,000\nfirst\n\n"
           "2\n00:00:05,000 --> 00:00:06,000\nsecond\n\n"
           "3\n00:00:07,000 --> 00:00:07,500\ngone\n")
    a = src(tmp_path / "s.mkv", srt=srt)
    segs = [seg(a, 4.0, 6.5, speed=0.5), seg(a, 0.5, 2.5)]
    out = tmp_path / "out.mkv"
    out.write_bytes(b"")
    written, warns = subs.write_sidecars(FF, segs, out, lambda f: ffmpeg.probe(FF, f))
    assert [Path(w).name for w in written] == ["out.eng.srt"] and not warns
    cues = subs.parse_srt(Path(written[0]).read_text(encoding="utf-8"))
    # seg 1 (4.0–6.5 at half speed → 5 s): "second" 5–6 → 2.0–4.0
    # seg 2 starts at 5.0: "first" 1–2 → 5.5–6.5; "gone" (7.0) is cut out
    assert [c.text for c in cues] == ["second", "first"]
    for c, (s, e) in zip(cues, [(2.0, 4.0), (5.5, 6.5)], strict=True):
        assert c.start == pytest.approx(s, abs=0.1) and c.end == pytest.approx(e, abs=0.1)


def test_forced_tracks_stay_forced():
    assert subs.sidecar_name("Took", ("eng", True, False)) == "Took.eng.forced.srt"
    assert subs.track_key({"language": "eng", "title": "SDH", "forced": False}) == \
        ("eng", False, True)


def test_hdr10_survives_an_edited_render(tmp_path):
    hdr = tmp_path / "hdr.mkv"
    md = ("G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50)")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=duration=4:size=256x144:rate=24", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=4:sample_rate=48000", "-c:v", "libx265",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
        "-x265-params", f"colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:"
                        f"hdr10=1:repeat-headers=1:master-display={md}:max-cll=1000,400",
        "-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
        "-c:a", "aac", "-shortest", str(hdr)], check=True)
    cut = CutPlan([seg(str(hdr), 0.5, 3.5, video={"grade": {"saturation": 0.9}})],
                  [], [], 0, 3, CutOptions())
    out, work = tmp_path / "graded.mkv", tmp_path / "w"
    work.mkdir()
    rp = plan.build_plan(cut, FF, out, work, lambda f: ffmpeg.probe(FF, f),
                         hdr_params=lambda f: ffmpeg.hdr10_params(FF, f))
    assert "libx265" in rp.command and any("master-display" in c for c in rp.command)
    run.execute(rp, work, lambda _p: None)
    info = ffmpeg.probe(FF, str(out))["video"]
    assert info["color_transfer"] == "smpte2084" and info["color_primaries"] == "bt2020"
    assert info["pix_fmt"] == "yuv420p10le"
    assert "master-display=G(13250,34500)" in ffmpeg.hdr10_params(FF, str(out))


@pytest.mark.parametrize("bad", [
    {"speed": 3}, {"speed": 0.2}, {"hold": 11}, {"video": {"crop": [0.5, 0, 0.6, 1]}},
    {"video": {"grade": {"exposure": 5}}}, {"video": {"filter": "eq"}},
    {"transition": {"type": "wipe"}}, {"j_cut": 0.5, "l_cut": 0.5},
    {"audio": {"src": "NOPE"}}, {"audio": {"start": 3}}, {"title": {"text": "a\nb"}},
    {"title": {"text": "x" * 81}}, {"audio": {"src": "S01E01", "file": "/etc/passwd"}},
])
def test_bounds_are_tight(bad):
    with pytest.raises(effects.EffectError):
        effects.validate(bad, "seg", {"S01E01": {"duration": 100}})


def test_effects_survive_recipe_validation():
    from fanscut.timeline import recipe

    doc = {"schema": 2, "kind": "timeline", "title": "T", "series": {"title": "T"},
           "sources": {"S01E01": {"duration": 100}, "S01E02": {"duration": 100}},
           "parts": [{"title": "P", "segments": [
               {"src": "S01E01", "start": 1, "end": 9, "speed": 0.9, "j_cut": 0.4,
                "audio": {"src": "S01E02", "start": 20, "fade_in": 1.5, "volume": 0.8},
                "video": {"fade_out": 1, "crop": [0.05, 0, 0.9, 1],
                          "grade": {"exposure": 0.1, "saturation": 0.95, "temperature": -3}},
                "transition": {"type": "dissolve", "duration": 0.5},
                "title": {"text": "Sixty years later", "duration": 3}}]}]}
    clean = recipe.validate(json.loads(json.dumps(doc)))["parts"][0]["segments"][0]
    assert clean["speed"] == 0.9 and clean["audio"]["src"] == "S01E02"
    assert clean["title"]["text"] == "Sixty years later"

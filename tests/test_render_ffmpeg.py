"""Real ffmpeg on tiny synthetic videos. Skipped when ffmpeg is not installed."""

import json
import subprocess
import threading
import wave

import numpy as np
import pytest

from fanscut.config import FFmpegCfg, FingerprintCfg
from fanscut.errors import FFmpegError, JobCancelled
from fanscut.fingerprint.detect import analyze_files
from fanscut.models import Segment
from fanscut.planner import CutOptions, CutPlan
from fanscut.render import ffmpeg
from fanscut.render import plan as rp
from fanscut.render import run as runner
from tests.conftest import make_video, needs_ffmpeg

pytestmark = needs_ffmpeg
FF = FFmpegCfg()


def cut_of(segs, **opts):
    return CutPlan(segs, [], [], 0, sum(s.duration for s in segs), CutOptions(**opts))


def probe_out(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_chapters",
         str(path)], capture_output=True, check=True, text=True).stdout
    return json.loads(out)


def render(tmp_path, segs, mode="auto", **opts):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    plan = rp.build_plan(cut_of(segs, **opts), FF, tmp_path / "out" / "cut.mkv", work,
                         lambda f: ffmpeg.probe(FF, f), {}, mode)
    seen = []
    runner.execute(plan, work, seen.append)
    return plan, seen


@pytest.fixture
def videos(tmp_path):
    return make_video(tmp_path / "a.mkv"), make_video(tmp_path / "b.mkv")


def test_probe(videos):
    info = ffmpeg.probe(FF, str(videos[0]))
    assert info["duration"] == pytest.approx(6, abs=0.3)
    assert info["video"]["codec"] == "h264" and info["video"]["width"] == 160
    assert info["audio"]["codec"] == "aac"


def test_prober_caches_in_sqlite(videos, db, monkeypatch):
    prober = ffmpeg.Prober(FF, db)
    first = prober(str(videos[0]))
    monkeypatch.setattr(ffmpeg, "probe", lambda *_a: pytest.fail("should hit the cache"))
    assert prober(str(videos[0])) == first


def test_probe_bad_file_raises(tmp_path):
    bad = tmp_path / "x.mkv"
    bad.write_text("not video")
    with pytest.raises(FFmpegError):
        ffmpeg.probe(FF, str(bad))


def test_copy_render_with_chapters(tmp_path, videos):
    a, b = videos
    segs = [Segment("S01E01", str(a), 1, 5), Segment("S01E02", str(b), 0, 4)]
    plan, progress = render(tmp_path, segs)
    assert plan.mode == "copy"
    out = tmp_path / "out" / "cut.mkv"
    assert out.is_file() and not (tmp_path / "out" / "cut.part.mkv").exists()
    meta = probe_out(out)
    assert float(meta["format"]["duration"]) == pytest.approx(8, abs=1.5)  # keyframe-loose
    assert len(meta["chapters"]) == 2
    assert progress and progress[-1] == 1.0


def test_encode_render_is_frame_accurate_and_mixes_sizes(tmp_path):
    a = make_video(tmp_path / "a.mkv")
    c = make_video(tmp_path / "c.mkv", size="240x160")
    segs = [Segment("S01E01", str(a), 0.5, 3.5), Segment("S01E02", str(c), 1.25, 4.25)]
    plan, _ = render(tmp_path, segs)
    assert plan.mode == "encode"
    meta = probe_out(tmp_path / "out" / "cut.mkv")
    assert float(meta["format"]["duration"]) == pytest.approx(6, abs=0.35)
    assert len(meta["chapters"]) == 2


def test_fade_render_runs(tmp_path, videos):
    a, b = videos
    segs = [Segment("S01E01", str(a), 0, 3), Segment("S01E02", str(b), 0, 3)]
    plan, _ = render(tmp_path, segs, transition="fade")
    assert plan.mode == "encode"
    assert float(probe_out(tmp_path / "out" / "cut.mkv")["format"]["duration"]) == pytest.approx(6, abs=0.4)


def test_mp4_container(tmp_path, videos):
    a, _ = videos
    work = tmp_path / "w"
    work.mkdir()
    plan = rp.build_plan(cut_of([Segment("S01E01", str(a), 0, 3)]), FFmpegCfg(container="mp4"),
                         tmp_path / "o" / "cut.mp4", work, lambda f: ffmpeg.probe(FF, f), {}, "encode")
    runner.execute(plan, work, lambda _p: None)
    assert (tmp_path / "o" / "cut.mp4").is_file()


def test_cancelled_render_leaves_no_output(tmp_path, videos):
    a, _ = videos
    work = tmp_path / "work"
    work.mkdir()
    plan = rp.build_plan(cut_of([Segment("S01E01", str(a), 0, 5)]), FF, tmp_path / "o" / "cut.mkv",
                         work, lambda f: ffmpeg.probe(FF, f), {}, "encode")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(JobCancelled):
        runner.execute(plan, work, lambda _p: None, cancel)
    assert not (tmp_path / "o" / "cut.mkv").exists()
    assert not (tmp_path / "o" / "cut.part.mkv").exists()


def test_failed_ffmpeg_reports_stderr(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    plan = rp.RenderPlan("copy", "", str(tmp_path / "o.mkv"), str(tmp_path / "o.part.mkv"),
                         ["ffmpeg", "-v", "error", "-progress", "pipe:1", "-i", "/no/such.mkv",
                          str(tmp_path / "o.part.mkv")], {}, 1.0)
    with pytest.raises(FFmpegError, match="No such file"):
        runner.execute(plan, work, lambda _p: None)


def test_missing_binary_is_a_clean_error():
    assert ffmpeg.version(FFmpegCfg(binary="definitely-not-ffmpeg")) is None
    with pytest.raises(FFmpegError, match="not found"):
        ffmpeg.run_with_progress(["definitely-not-ffmpeg"], 1.0, lambda _p: None)


def test_decode_audio_length(videos):
    pcm = ffmpeg.decode_audio(FF, str(videos[0]), 1.0, 3.0, 8000)
    assert len(pcm) == pytest.approx(24000, abs=800)
    assert np.abs(pcm).max() > 0.05


def write_wav(path, samples, sr=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(samples, -1, 1) * 32000).astype("<i2").tobytes())


def test_detects_shared_intro_in_real_files(tmp_path):
    rng = np.random.default_rng(5)
    theme = rng.normal(0, 0.1, 8000 * 12).astype(np.float32)
    offsets = [3, 9, 6]
    files = []
    for n, off in enumerate(offsets):
        audio = np.concatenate([rng.normal(0, 0.1, 8000 * off), theme,
                                rng.normal(0, 0.1, 8000 * (30 - off - 12))]).astype(np.float32)
        write_wav(tmp_path / f"{n}.wav", audio)
        files.append((f"S01E0{n + 1}", str(make_video(tmp_path / f"{n}.mkv", 30, wav=tmp_path / f"{n}.wav")), 30.0))
    fp = FingerprintCfg(head_minutes=0.5, tail_minutes=0.2, min_segment_seconds=8, threshold=0.7)
    found = analyze_files(files, fp, FF)
    for (eid, _, _), off in zip(files, offsets, strict=True):
        s, e = found[eid]["intro"]
        assert abs(s - off) < 1.0 and abs(e - (off + 12)) < 1.0

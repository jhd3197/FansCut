import re
from pathlib import Path

import pytest

from fanscut.config import FFmpegCfg
from fanscut.models import Segment
from fanscut.planner import CutOptions, CutPlan
from fanscut.render import plan as rp

VID = {"codec": "h264", "width": 1280, "height": 720, "pix_fmt": "yuv420p", "fps": 24.0}
AUD = {"codec": "aac", "sample_rate": 48000, "channels": 2}


def info(**over):
    return {"duration": 100.0, "video": {**VID, **over.get("video", {})},
            **({} if over.get("no_audio") else {"audio": {**AUD, **over.get("audio", {})}})}


def cut_of(segs, **opts):
    return CutPlan(segs, sorted({s.episode_id for s in segs}), [], 0, sum(s.duration for s in segs),
                   CutOptions(**opts))


SEGS = [Segment("S01E01", "/m/a.mkv", 0, 40), Segment("S01E01", "/m/a.mkv", 50, 90),
        Segment("S01E02", "/m/b.mkv", 5, 95)]
OUT = Path("/out/Harbor Lights - fan cut.mkv")
WORK = Path("/work")


def build(segs=SEGS, probe=None, **kw):
    return rp.build_plan(cut_of(segs, **kw.pop("opts", {})), FFmpegCfg(), OUT, WORK,
                         probe or (lambda _f: info()), {"S01E01": "S01E01 Pilot"}, **kw)


def test_copy_mode_concat_list_and_chapters():
    plan = build()
    assert plan.mode == "copy"
    lines = plan.files["concat.txt"].splitlines()
    assert lines[:3] == ["file '/m/a.mkv'", "inpoint 0.000", "outpoint 40.000"]
    assert lines[-3:] == ["file '/m/b.mkv'", "inpoint 5.000", "outpoint 95.000"]
    assert "-c" in plan.command and plan.command[plan.command.index("-c") + 1] == "copy"
    assert plan.command[-1] == str(OUT.with_name("Harbor Lights - fan cut.part.mkv"))
    assert [(c["title"], c["start"], c["end"]) for c in plan.chapters] == [
        ("S01E01 Pilot", 0, 80), ("S01E02", 80, 170)]
    assert plan.total_seconds == 170
    assert "START=80000" in plan.files["chapters.ffmeta"]
    assert "-progress" in plan.command


def test_contiguous_segments_of_one_file_are_joined():
    segs = [Segment("S01E01", "/m/a.mkv", 0, 40), Segment("S01E01", "/m/a.mkv", 40, 80)]
    plan = build(segs)
    assert plan.files["concat.txt"].count("file ") == 1
    assert "outpoint 80.000" in plan.files["concat.txt"]


def test_single_quote_in_path_is_escaped():
    plan = build([Segment("S01E01", "/m/Kaito's/a.mkv", 0, 10)])
    assert "file '/m/Kaito'\\''s/a.mkv'" in plan.files["concat.txt"]


def test_chapter_titles_escape_metadata_syntax():
    plan = rp.build_plan(cut_of(SEGS[:1]), FFmpegCfg(), OUT, WORK, lambda _f: info(),
                         {"S01E01": "A=B;C#D"})
    assert r"title=A\=B\;C\#D" in plan.files["chapters.ffmeta"]


@pytest.mark.parametrize(("other", "word"), [
    (info(video={"codec": "hevc"}), "differ"),
    (info(video={"width": 1920, "height": 1080}), "differ"),
    (info(audio={"sample_rate": 44100}), "differ"),
])
def test_mismatched_files_fall_back_to_encode(other, word):
    plan = build(probe=lambda f: other if f.endswith("b.mkv") else info())
    assert plan.mode == "encode" and word in plan.reason
    assert plan.command[plan.command.index("-c:v") + 1] == "libx264"
    assert "-filter_complex_script" in plan.command


def test_encode_filter_script_trims_each_segment_and_concats():
    plan = build(mode="encode")
    f = plan.files["filter.txt"]
    assert f.count("atrim=start=") == 3
    assert "[1:v:0]trim=start=5.000:end=95.000" in f  # file b is the second input
    assert "concat=n=3:v=1:a=1[v][a]" in f
    assert "scale=1280:720" in f and "fade" not in f
    assert plan.command.count("-i") == 3  # two media files + the chapter metadata


def test_fade_transition_forces_encode_and_fades_only_at_episode_edges():
    plan = build(opts={"transition": "fade"})
    f = plan.files["filter.txt"]
    assert plan.mode == "encode" and "fade" in plan.reason
    assert len(re.findall(r"(?<!a)fade=t=in", f)) == 1  # only the second episode fades in
    assert len(re.findall(r"(?<!a)fade=t=out", f)) == 1  # only the first fades out
    assert "afade=t=in" in f and "afade=t=out" in f


def test_copy_refused_when_impossible():
    with pytest.raises(ValueError, match="cannot stream copy"):
        build(opts={"transition": "fade"}, mode="copy")
    with pytest.raises(ValueError, match="cannot stream copy"):
        build(probe=lambda f: info(video={"codec": "hevc"}) if f.endswith("b.mkv") else info(),
              mode="copy")


def test_nvenc_and_mp4_options():
    cfg = FFmpegCfg(nvenc=True, container="mp4", crf=23)
    plan = rp.build_plan(cut_of(SEGS), cfg, Path("/out/x.mp4"), WORK, lambda _f: info(), mode="encode")
    assert plan.command[plan.command.index("-c:v") + 1] == "h264_nvenc"
    assert "-cq" in plan.command and "+faststart" in plan.command


def test_no_audio_drops_audio_with_warning():
    plan = build(probe=lambda _f: info(no_audio=True), mode="encode")
    assert plan.warnings and "[a]" not in plan.files["filter.txt"] and "-c:a" in plan.command
    assert "0:a:0" not in " ".join(build(probe=lambda _f: info(no_audio=True)).command)


def test_empty_cut_is_an_error():
    with pytest.raises(ValueError, match="empty"):
        build([])


def test_safe_name_and_unique_path(tmp_path):
    assert rp.safe_name('Harbor: Lights? <2>/x') == "Harbor Lights 2 x"
    assert rp.safe_name("...") == "fan cut"
    first = rp.unique_path(tmp_path, "cut", "mkv")
    first.touch()
    assert rp.unique_path(tmp_path, "cut", "mkv").name == "cut (2).mkv"


def test_to_dict_is_json_ready():
    import json
    json.dumps(build().to_dict())

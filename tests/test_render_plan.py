import os
import re
from pathlib import Path

import pytest

from fanscut.config import FFmpegCfg
from fanscut.models import Segment
from fanscut.planner import CutOptions, CutPlan
from fanscut.render import plan as rp


def q(path):
    """The concat-list line for a path, the way _concat_quote writes it on this OS."""
    return "file '" + os.path.abspath(path).replace("'", "'\\''") + "'"

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
    assert lines[:3] == [q("/m/a.mkv"), "inpoint 0.000", "outpoint 40.000"]
    assert lines[-3:] == [q("/m/b.mkv"), "inpoint 5.000", "outpoint 95.000"]
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
    assert q("/m/Kaito's/a.mkv") in plan.files["concat.txt"]
    assert "'\\''" in plan.files["concat.txt"]  # the quote really is escaped


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
    assert "concat=n=3:v=1:a=0[v]" in f  # video concats in one go
    assert "[a0][a1]concat=n=2:v=0:a=1[ac1]" in f  # audio chains pairwise
    assert "scale=1280:720" in f and "fade" not in f
    assert plan.command.count("-i") == 3  # two media files + the chapter metadata


def test_xfade_extends_previous_audio_and_crossfades():
    segs = [Segment("S01E01", "/m/a.mkv", 0, 40), Segment("S01E01", "/m/a.mkv", 50, 90)]
    segs[1].xfade_ms = 400
    plan = build(segs, mode="encode")
    f = plan.files["filter.txt"]
    assert "atrim=start=0.000:end=40.400" in f  # outgoing audio runs 0.4 s past its video cut
    assert "acrossfade=d=0.400" in f
    with pytest.raises(ValueError, match="crossfade"):
        build(segs, mode="copy")
    # without the probe durations there's no room info, so no extension
    plan2 = build(segs, probe=lambda _f: info(), mode="encode")
    assert "atrim=start=0.000:end=40.400" in plan2.files["filter.txt"]


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


UHD_HDR = {"codec": "hevc", "width": 3840, "height": 1600, "pix_fmt": "yuv420p10le",
           "fps": 23.976, "color_transfer": "smpte2084"}


def test_fast_quality_is_1080p_sdr_h264_and_tone_maps_only_hdr():
    probe = lambda f: info(video=UHD_HDR) if f == "/m/a.mkv" else info()  # noqa: E731
    best = build(probe=probe)
    assert "libx265" in best.command and "hdr10=1" in " ".join(best.command)
    fast = build(probe=probe, quality="fast")
    cmd = " ".join(fast.command)
    assert fast.mode == "encode" and "libx264" in fast.command and "bt2020" not in cmd
    assert fast.reason == "files differ in codec, size, frame rate or audio format"
    assert build(probe=lambda f: info(video=UHD_HDR), quality="fast").reason == "fast quality: 1080p"
    script = fast.files["filter.txt"]
    chains = [line for line in script.splitlines() if line.startswith(("[0:v", "[1:v"))]
    a_chain, b_chain = chains[0], chains[-1]  # a.mkv (HDR) first, b.mkv (SDR) last
    assert "scale=1920:800" in a_chain and "tonemap=hable" in a_chain  # the HDR 4K file
    assert "tonemap" not in b_chain  # the SDR one is only scaled
    assert any("fast quality: 1920x800 H.264, HDR tone-mapped" in w for w in fast.warnings)


def test_fast_quality_changes_nothing_for_small_sdr_files():
    assert rp.fast_size(1280, 720) == (1280, 720)  # never upscaled
    assert rp.fast_size(3840, 2160) == (1920, 1080) and rp.fast_size(4096, 1716) == (1920, 804)
    assert not rp.fast_differs([info()])
    plan = build(quality="fast")
    assert plan.mode == "copy"  # same as best: nothing to make smaller
    with pytest.raises(ValueError, match="fast quality"):
        build(probe=lambda f: info(video=UHD_HDR), quality="fast", mode="copy")


def test_fast_quality_tone_maps_edited_segments_too():
    probe = lambda f: info(video=UHD_HDR)  # noqa: E731
    seg = Segment("S01E01", "/m/a.mkv", 0, 40)
    seg.fx = {"video": {"grade": {"saturation": 0.9}}}
    fast = build(segs=[seg, Segment("S01E01", "/m/a.mkv", 50, 90)], probe=probe, quality="fast")
    assert fast.files["filter.txt"].count("tonemap=hable") == 2
    assert "scale=1920:800" in fast.files["filter.txt"] and "libx264" in fast.command


def test_input_clusters_seek_near_segments_and_cap_the_count():
    near = [Segment("A", "/m/a.mkv", 600, 610), Segment("A", "/m/a.mkv", 650, 660)]
    far = Segment("A", "/m/a.mkv", 3000, 3010)
    other = Segment("B", "/m/b.mkv", 5, 9)
    inputs, at, bridge = rp.input_clusters([*near, far, other])
    assert inputs == [("/m/a.mkv", 585.0), ("/m/a.mkv", 2985.0), ("/m/b.mkv", 0.0)]
    assert at == [(0, 585.0), (0, 585.0), (1, 2985.0), (2, 0.0)] and bridge == [None] * 4

    music = Segment("A", "/m/a.mkv", 600, 610)
    music.audio_file, music.fx = "/m/score.mkv", {"audio": {"src": "S", "start": 120.0}}
    inputs, _at, bridge = rp.input_clusters([music])
    assert inputs[-1] == ("/m/score.mkv", 105.0) and bridge == [(1, 105.0)]

    many = [Segment("A", "/m/a.mkv", k * 500, k * 500 + 5) for k in range(60)]
    inputs, at, _b = rp.input_clusters(many)
    assert inputs == [("/m/a.mkv", 0.0)]  # past MAX_INPUTS: one input per file


def test_encode_command_seeks_and_trims_relative_to_the_seek():
    segs = [Segment("S01E01", "/m/a.mkv", 1200, 1230), Segment("S01E02", "/m/b.mkv", 5, 95)]
    plan = build(segs=segs, probe=lambda f: info(video={"codec": "hevc"}) if f == "/m/a.mkv" else info())
    cmd = plan.command
    i = cmd.index("/m/a.mkv")
    assert cmd[i - 3:i] == ["-ss", "1185.000", "-i"]
    assert "trim=start=15.000:end=45.000" in plan.files["filter.txt"]
    assert "trim=start=5.000:end=95.000" in plan.files["filter.txt"]  # b.mkv: no seek

"""EffectCraft for graded segments: translation, project steps, the join contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from fanscut.config import FFmpegCfg
from fanscut.models import Segment
from fanscut.render import effectcraft, ffmpeg
from tests.conftest import needs_ffmpeg

FF = FFmpegCfg(preset="ultrafast")
CLI = os.environ.get("FANSCUT_TEST_EFFECTCRAFT", "")


def test_lumetri_mapping():
    p = effectcraft.lumetri({"exposure": 0.1, "saturation": 0.95, "temperature": -3,
                             "contrast": 1.2})
    assert p == {"basicCorrection/tone/exposure": 0.1, "basicCorrection/saturation": 95.0,
                 "basicCorrection/whiteBalance/temperature": -6.0,
                 "basicCorrection/tone/contrast": 20.0}


@pytest.mark.parametrize("transfer, space", [("smpte2084", "rec2100pq"),
                                             ("arib-std-b67", "rec2100hlg"), ("bt709", "rec709")])
def test_output_space_follows_the_source(transfer, space):
    assert effectcraft.output_space({"video": {"color_transfer": transfer}}) == space


def test_project_steps_are_commands_not_templates(tmp_path):
    steps = effectcraft.project_commands(tmp_path / "p.mp4",
                                         {"video": {"color_transfer": "smpte2084"}},
                                         {"exposure": 0.1})
    assert [s["command"] for s in steps] == ["file.projectSettings", "file.import",
                                             "effect.apply", "prop.set"]
    assert steps[0]["params"]["outputSpace"] == "rec2100pq"
    assert steps[3]["params"] == {"path": "effects/#1/basicCorrection/tone/exposure",
                                  "value": 0.1}


@needs_ffmpeg
def test_cut_piece_reencodes_non_hevc_to_main10(tmp_path):
    from tests.test_effects_render import src

    f = src(tmp_path / "a.mkv", seconds=6)
    seg = Segment("S", f, 2.0, 4.0, fx={"video": {"grade": {"exposure": 0.2}}})
    piece, offset = effectcraft.cut_piece(FF, seg, ffmpeg.probe(FF, f), tmp_path, "p")
    info = ffmpeg.probe(FF, str(piece))
    assert info["video"]["codec"] == "hevc" and info["video"]["pix_fmt"] == "yuv420p10le"
    assert offset == pytest.approx(1.0, abs=0.05)  # one second of handle before the start


def test_missing_cli_is_named(tmp_path):
    with pytest.raises(effectcraft.EffectCraftError, match="not found"):
        effectcraft._run([str(tmp_path / "nope.exe")], timeout=5)


@pytest.mark.skipif(not CLI, reason="set FANSCUT_TEST_EFFECTCRAFT to effectcraft-cli")
@needs_ffmpeg
def test_graded_segment_round_trip(tmp_path):
    from tests.test_effects_render import src

    f = src(tmp_path / "a.mkv", seconds=5, size="320x180")
    seg = Segment("S", f, 1.0, 3.0, fx={"video": {"grade": {"saturation": 0.0}}})
    out = effectcraft.render_segment(CLI, FF, seg, ffmpeg.probe(FF, f), tmp_path, "g",
                                     timeout=900)
    assert Path(out.file).is_file() and out.audio_file == f
    assert "grade" not in (out.fx.get("video") or {})
    assert out.fx["audio"]["start"] == 1.0
    assert float(ffmpeg.probe(FF, out.file)["duration"]) == pytest.approx(2.0, abs=0.15)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out.file, "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "yuv444p", "-"],
                         capture_output=True, check=True).stdout
    import numpy as np

    u = np.frombuffer(raw, np.uint8)[320 * 180:2 * 320 * 180]
    assert abs(float(u.mean()) - 128) < 6  # Lumetri saturation 0 took effect

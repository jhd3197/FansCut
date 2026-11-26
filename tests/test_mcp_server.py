"""The MCP tools, driven against a test FansCut (no MCP transport involved)."""

from __future__ import annotations

import re

import pytest

from fanscut import mcp_server as m
from tests.conftest import needs_ffmpeg, needs_prompture_video
from tests.test_scenes import film_dir, sc  # noqa: F401 - fixtures

pytestmark = [needs_ffmpeg, needs_prompture_video]


@pytest.fixture
def tools(sc):  # noqa: F811
    m.use_client(sc)
    yield sc
    m.use_client(None)  # type: ignore[arg-type]


def test_tagging_session_through_the_tools(tools):
    key = tools.key
    assert "not indexed" in m.list_films("Snow Saga")
    assert "Not indexed yet" in m.film_overview(key)

    started = m.index_film(key)
    job_id = re.search(r"job (\w+)", started).group(1)
    tools.run()  # the embedded Marcarr's job
    assert "done" in m.job_status(job_id)

    over = m.film_overview(key)
    assert "3 shots" in over and "subtitles: sidecar (4 lines)" in over
    assert m.read_dialogue(key, 0, 10).splitlines()[0] == "[0:00:01] Happy Christmas, Harry."
    assert m.camera_cuts(key, 0, 30).count("\n") == 2

    out = m.propose_scene(key, 0.5, 9.5, ["Christmas"], ["Harry"], "presents", 0.9)
    assert "[proposed] christmas" in out and "Harry" in out
    tid = out.split()[1]
    assert tid in m.list_tags(key, "christmas", "proposed")
    assert "[accepted]" in m.update_scene(tid, status="accepted", summary="opening presents")
    assert "opening presents" in m.collection_tags("Snow Saga", "christmas")
    assert "Created timeline" in m.make_timeline("Snow Saga", "christmas", "Snowy bits")

    with pytest.raises(RuntimeError, match="400"):
        m.make_timeline("Snow Saga", "feast")


def test_look_returns_the_grid_as_an_image(tools):
    pytest.importorskip("mcp")
    key = tools.key
    text, img = m.look(key, 0, 30, cols=3, rows=1)
    assert text.startswith("Tiles: #1 0:00:05 (5.0s)")
    assert img.data[:2] == b"\xff\xd8"
    assert m.frame(key, 12)[1].data[:2] == b"\xff\xd8"


def test_tools_are_described_for_the_model():
    pytest.importorskip("prompture")
    defs = {d.name: d for d in m.tool_definitions()}
    assert set(defs) == {fn.__name__ for fn in m.TOOLS}
    assert "camera cut" in defs["propose_scene"].description

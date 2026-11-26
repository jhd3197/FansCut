"""A film's analysis steps for the film page: status and queueing what is missing."""

from __future__ import annotations

from tests.conftest import needs_ffmpeg, needs_prompture_video
from tests.test_scenes import film_dir, sc  # noqa: F401 - fixtures


def _states(c, key):
    res = c.get("/api/scenes/analysis", params={"key": key}).json()
    return res, {s["id"]: s["state"] for s in res["steps"]}


@needs_ffmpeg
@needs_prompture_video
def test_analysis_steps_and_queueing(sc):  # noqa: F811
    key = sc.key
    res, states = _states(sc, key)
    assert [s["id"] for s in res["steps"]] == ["shots", "faces", "speech", "tags"]
    assert states == {"shots": "todo", "faces": "todo", "speech": "todo", "tags": "off"}
    assert res["analysed"] is False and res["ai"] is False and res["video"] is None

    # one Marcarr job runs every step that is left
    out = sc.post("/api/scenes/analyse", json={"key": key}).json()
    assert [j["kind"] for j in out["queued"]] == ["marcarr"]
    assert out["video"] and out["queued"][0]["steps"][:3] == ["shots", "subtitles", "scenes"]
    _, states = _states(sc, key)
    assert states["shots"] == "queued" and states["speech"] == "queued"
    # asking again queues nothing new
    assert sc.post("/api/scenes/analyse", json={"key": key}).json()["queued"] == []

    sc.run()
    res, states = _states(sc, key)
    assert states["shots"] == "done" and states["speech"] == "done"
    assert next(s for s in res["steps"] if s["id"] == "shots")["detail"] == "3 shots, 1 scene"
    assert states["tags"] == "off" and res["analysed"] is False  # no AI model

    sc.core.cfg.ai.model = "ollama/test"
    sc.core.scenes.link.apply_config(sc.core.cfg)
    res, states = _states(sc, key)
    assert states["tags"] == "todo" and res["ai"] is True

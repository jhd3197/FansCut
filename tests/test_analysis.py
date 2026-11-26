"""A film's analysis steps for the film page: status and queueing what is missing."""

from __future__ import annotations

from tests.conftest import needs_ffmpeg
from tests.test_scenes import film_dir, sc  # noqa: F401 - fixtures


def _states(c, key):
    res = c.get("/api/scenes/analysis", params={"key": key}).json()
    return res, {s["id"]: s["state"] for s in res["steps"]}


@needs_ffmpeg
def test_analysis_steps_and_queueing(sc):  # noqa: F811
    key = sc.key
    res, states = _states(sc, key)
    assert [s["id"] for s in res["steps"]] == ["shots", "faces", "speech", "tags"]
    assert states == {"shots": "todo", "faces": "todo", "speech": "todo", "tags": "off"}
    assert res["analysed"] is False and res["ai"] is False

    # with a model: the index, then labelling after it (the index queues the signals)
    sc.core.cfg.ai.model = "ollama/test"
    out = sc.post("/api/scenes/analyse", json={"key": key}).json()
    kinds = [j["kind"] for j in out["queued"]]
    assert kinds == ["scene_index", "scene_label"]
    index_id = out["queued"][0]["id"]
    assert out["queued"][1]["payload"]["after"] == [index_id]
    _, states = _states(sc, key)
    assert states["shots"] == "queued" and states["tags"] == "queued"
    # asking again queues nothing new
    assert sc.post("/api/scenes/analyse", json={"key": key}).json()["queued"] == []

    for j in out["queued"]:
        sc.post(f"/api/jobs/{j['id']}/cancel")
    store = sc.core.scenes.store
    store.save_index(key, {"shots": [[0, 10], [10, 30]], "scenes": [{"start": 0, "end": 30}],
                           "lines": [], "subtitles": {"kind": "none", "lines": 0}})
    res, states = _states(sc, key)
    assert states["shots"] == "done" and states["speech"] == "todo"
    assert states["tags"] == "todo" and res["analysed"] is False

    for i in range(5):
        store.add_tag(key, 671, i, i + 1, ["snow"], [], source="user", status="accepted")
    store.add_tag(key, 671, 20, 25, ["fc:filler"], [], source="user", status="accepted")
    res, states = _states(sc, key)
    assert states["tags"] == "done" and res["analysed"] is True
    assert next(s for s in res["steps"] if s["id"] == "tags")["detail"] == "5 moments"

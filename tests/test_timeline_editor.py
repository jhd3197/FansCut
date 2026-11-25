"""Preview clips, find-similar plumbing, and the AI timeline outline."""

import json
from pathlib import Path

from fanscut import ai
from tests.conftest import make_video, needs_ffmpeg
from tests.test_ai import LLM
from tests.test_api import E


@needs_ffmpeg
def test_preview_clip_real(cfg, tmp_path):
    from fastapi.testclient import TestClient

    from fanscut.server import create_app

    root = tmp_path / "media" / "Tide Pool"
    root.mkdir(parents=True)
    make_video(root / "Tide Pool - S01E01.mkv", seconds=12)
    cfg.library.folder.path = str(tmp_path / "media")
    cfg.catalogues.offline = True
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        key = "folder:Tide Pool"
        r = c.get(f"/api/series/{key}/episodes/S01E01/preview?t=3")
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        assert b"ftyp" in r.content[:32]
        cached = list((tmp_path / "data" / "previews").glob("*.mp4"))
        assert len(cached) == 1
        r2 = c.get(f"/api/series/{key}/episodes/S01E01/preview?t=3")
        assert len(list((tmp_path / "data" / "previews").glob("*.mp4"))) == 1  # cache hit
        assert r2.content == r.content
        assert c.get(f"/api/series/{key}/episodes/S01E09/preview?t=3").status_code == 404


def test_find_similar_job(client, monkeypatch):
    core = client.app_ref.state.core
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    def fake_decode(_ff, path, start, duration, rate):
        return __import__("numpy").zeros(int(rate * duration), dtype="float32")

    def fake_find_shared(a, b, threshold, mn, mx):
        from fanscut.fingerprint.match import Match
        return [Match(0.0, 30.0, 412.0, 442.0, 0.9)]

    monkeypatch.setattr("fanscut.core.ffmpeg.decode_audio", fake_decode)
    # core imports find_shared inside the method, so patch the module attr
    import fanscut.fingerprint.match as m
    monkeypatch.setattr(m, "find_shared", fake_find_shared)
    job = client.post(f"{E}/find-similar",
                      json={"episode_id": "S01E01", "start": 100, "end": 130}).json()
    core.worker.run_pending()
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done"
    matches = done["result"]["matches"]
    assert {"episode_id": "S01E02", "start": 412.0, "end": 442.0} in matches
    assert all(m["episode_id"] != "S01E01" for m in matches)


def test_find_similar_validates(client):
    assert client.post(f"{E}/find-similar",
                       json={"episode_id": "S01E01", "start": 100, "end": 100.5}
                       ).status_code == 400


OUTLINE = [{"part": "Year -1003", "scenes": [
    {"src": "S01E04", "approx": "05:10", "desc": "the dock fight"},
    {"src": "S01E01", "approx": "0:12:00"}]}]


def test_outline_parsing():
    assert ai.parse_approx("05:10") == 310.0 and ai.parse_approx("1:02:03") == 3723.0
    clean = ai.validate_outline(OUTLINE)
    assert clean[0]["scenes"][0] == {"src": "S01E04", "approx": 310.0, "desc": "the dock fight"}
    assert clean[0]["scenes"][1]["approx"] == 720.0


def test_outline_endpoint(client, app_cfg, fake_llm):
    app_cfg.ai.base_url = LLM
    app_cfg.ai.model = "test-model"
    client.get(f"{E}/episodes")  # warm the library cache before the nested mock
    with fake_llm.answering(json.dumps(OUTLINE)):
        r = client.post(f"{E}/timeline/outline", json={"notes": "chronology…"})
        assert r.status_code == 200
        parts = r.json()["outline"]
        assert parts[0]["part"] == "Year -1003"
        assert parts[0]["scenes"][0]["approx"] == 310.0


def test_outline_drops_unknown_sources(client, app_cfg, fake_llm):
    app_cfg.ai.base_url = LLM
    app_cfg.ai.model = "test-model"
    client.get(f"{E}/episodes")
    doc = [{"part": "p", "scenes": [{"src": "S09E99", "approx": "01:00"},
                                    {"src": "S01E01", "approx": "01:00"}]}]
    with fake_llm.answering(json.dumps(doc)):
        parts = client.post(f"{E}/timeline/outline", json={"notes": "x"}).json()["outline"]
        assert [s["src"] for s in parts[0]["scenes"]] == ["S01E01"]

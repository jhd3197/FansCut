"""AI posters: a take from the cut's stills, keep or discard it, use it for builds."""

from __future__ import annotations

import base64
import io
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fanscut import posters
from fanscut.server import create_app

DOC = {"title": "Christmas at Hogwarts", "series": {"title": "Harry Potter"},
       "sources": {"M671": {"kind": "movie", "tmdb": 671}},
       "parts": [{"segments": [{"src": "M671", "start": 10, "end": 40}]}]}


def _jpeg(color: str = "red") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(out, "JPEG")
    return out.getvalue()


class FakeDriver:
    calls: list = []

    def edit_image(self, images, prompt, options):
        FakeDriver.calls.append((images, prompt, options))
        png = io.BytesIO()
        Image.new("RGB", (32, 48), "blue").save(png, "PNG")
        return {"images": [SimpleNamespace(data=base64.b64encode(png.getvalue()).decode())],
                "meta": {"cost": 0.05}}


@pytest.fixture
def pc(cfg, tmp_path, monkeypatch):
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        core = c.app.state.core
        still = tmp_path / "still.jpg"
        still.write_bytes(_jpeg())
        monkeypatch.setattr(core, "get_timeline",
                            lambda tid, with_doc=True: {"id": tid, "doc": DOC})
        monkeypatch.setattr(core, "timeline_source_frame", lambda tid, src, t: still)
        monkeypatch.setattr(core.scenes, "films", lambda collection=None: [
            {"key": "radarr:1", "tmdb_id": 671}])
        core.scenes.store.add_tag("radarr:1", 671, 0, 30, ["christmas", "snow"], [],
                                  source="user", status="accepted")
        import prompture.drivers.img_gen_registry as reg

        monkeypatch.setattr(reg, "get_img_gen_driver_for_model", lambda model: FakeDriver())
        c.core = core
        yield c


def _wait(c, tid="t1"):
    for _ in range(100):
        st = c.get(f"/api/timelines/{tid}/poster/ai").json()
        if (st["task"] or {}).get("status") != "running":
            return st
        time.sleep(0.05)
    raise AssertionError("the take never finished")


def test_prompt_names_the_cut_and_its_tags():
    p = posters.prompt_for("Christmas at Hogwarts", "Harry Potter", ["christmas", "snow"])
    assert '"Christmas at Hogwarts"' in p and "from Harry Potter" in p and "christmas, snow" in p
    assert "No title text" in posters.prompt_for("X", "", [], with_title=False)


def test_take_keep_discard_and_build(pc, tmp_path):
    FakeDriver.calls.clear()
    assert pc.get("/api/timelines/t1/poster").status_code == 404
    bad = pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "nope", "t": 1}]})
    assert bad.status_code == 400
    r = pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "M671", "t": 20}] * 6})
    assert r.status_code == 202
    st = _wait(pc)
    assert st["task"]["status"] == "done", st
    images, prompt, options = FakeDriver.calls[0]
    assert len(images) == posters.MAX_STILLS and images[0][:4] == b"\x89PNG"
    assert "christmas, snow" in prompt and options["size"] == "1024x1536"
    take = st["takes"][0]
    assert take["cost"] == 0.05 and take["tags"] == ["christmas", "snow"]
    assert pc.get(f"/api/timelines/t1/poster/ai/{take['name']}").content[:4] == b"\x89PNG"

    st = pc.post(f"/api/timelines/t1/poster/ai/{take['name']}/keep").json()
    assert st["chosen"] == take["name"]
    assert pc.get("/api/timelines/t1/poster").status_code == 200
    out = tmp_path / "show"
    out.mkdir()
    assert pc.core.posters.write_for_build("t1", out) == str(out / "poster.jpg")
    assert Image.open(out / "poster.jpg").format == "JPEG"

    assert pc.delete("/api/timelines/t1/poster").json()["chosen"] is None
    assert pc.core.posters.write_for_build("t1", out) is None  # back to the usual poster
    st = pc.delete(f"/api/timelines/t1/poster/ai/{take['name']}").json()
    assert st["takes"] == []
    assert pc.get("/api/timelines/t1/poster/ai/../../config.yaml").status_code == 404


def test_no_model_no_posters(pc):
    pc.core.cfg.posters.ai_model = ""
    r = pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "M671", "t": 20}]})
    assert r.status_code == 400


def test_image_bytes_reads_every_shape():
    raw = b"\x89PNGdata"
    b64 = base64.b64encode(raw).decode()
    assert posters.image_bytes(raw) == raw
    assert posters.image_bytes(b64) == raw
    assert posters.image_bytes(f"data:image/png;base64,{b64}") == raw
    assert posters.image_bytes(SimpleNamespace(data=b64)) == raw
    with pytest.raises(Exception, match="no image"):
        posters.image_bytes(None)


def test_folder_rejects_odd_ids(pc):
    with pytest.raises(Exception, match="no such cut"):
        pc.core.posters.folder("../x")
    assert isinstance(pc.core.posters.folder("t1"), Path)

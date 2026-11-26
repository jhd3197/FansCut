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
    refuse = False

    def generate_image(self, prompt, options):
        return self.edit_image([], prompt, options, words_only=True)

    def edit_image(self, images, prompt, options, words_only=False):
        if FakeDriver.refuse and not words_only:
            raise RuntimeError("Error code: 400 - moderation_blocked")
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
        monkeypatch.setattr(core.scenes.store, "signals", lambda key: {
            "signals": {"no-face": [[18.0, 22.0], [30.0, 36.0]]}})
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
    # t=20 has no face; the other copies of it collapse into one still
    assert len(images) == 1 and images[0][:4] == b"\x89PNG"
    assert "do not depict any real" in prompt
    assert "christmas, snow" in prompt and options["size"] == "1024x1536"
    take = st["takes"][0]
    assert take["cost"] == 0.05 and take["tags"] == ["christmas", "snow"]
    assert pc.get(f"/api/timelines/t1/poster/ai/{take['name']}").content[:4] == b"\x89PNG"

    # with nothing chosen yet, the first take is the cut's poster straight away
    assert st["chosen"] == take["name"]
    assert pc.get("/api/timelines/t1/poster").content[1:4] == b"PNG"
    st = pc.post(f"/api/timelines/t1/poster/ai/{take['name']}/keep").json()
    assert st["chosen"] == take["name"]
    assert pc.get("/api/timelines/t1/poster").status_code == 200
    out = tmp_path / "show"
    out.mkdir()
    written = pc.core.posters.write_for_build("t1", out)
    assert written == str(out / "poster.jpg")
    assert Image.open(out / "poster.jpg").format == "JPEG"

    cleared = pc.delete("/api/timelines/t1/poster").json()
    assert cleared["chosen"] is None
    written = pc.core.posters.write_for_build("t1", out)
    assert written is None  # back to the usual poster
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


def test_stills_move_to_faceless_moments(pc):
    got = pc.core.posters.faceless("t1", [("M671", 12.0), ("M671", 20.0), ("nope", 5.0)])
    assert got == [("M671", 33.0), ("M671", 20.0)]  # 12 s had a face: the longest no-face bit


def test_a_refusal_draws_from_words(pc):
    FakeDriver.calls.clear()
    FakeDriver.refuse = True
    try:
        pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "M671", "t": 20}]})
        st = _wait(pc)
    finally:
        FakeDriver.refuse = False
    assert st["task"]["status"] == "done", st
    images, prompt, _ = FakeDriver.calls[0]
    assert images == [] and "reference images" not in prompt


def test_a_later_take_waits_to_be_kept(pc):
    pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "M671", "t": 20}]})
    first = _wait(pc)["chosen"]
    time.sleep(0.01)
    pc.post("/api/timelines/t1/poster/ai", json={"stills": [{"src": "M671", "t": 20}]})
    st = _wait(pc)
    assert len(st["takes"]) == 2 and st["chosen"] == first  # the new one is only offered


def test_a_poster_drawn_on_the_page_is_used(pc, tmp_path):
    out = io.BytesIO()
    Image.new("RGB", (60, 90), "green").save(out, "JPEG")
    url = "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()
    st = pc.put("/api/timelines/t1/poster", json={"kind": "frame", "image": url}).json()
    assert st["chosen"].startswith("frame-")
    assert pc.get("/api/timelines/t1/poster").content[1:4] == b"PNG"
    show = tmp_path / "show2"
    show.mkdir()
    assert pc.core.posters.write_for_build("t1", show) == str(show / "poster.jpg")
    st = pc.put("/api/timelines/t1/poster", json={"kind": "title", "image": url}).json()
    assert st["chosen"].startswith("title-")
    assert pc.put("/api/timelines/t1/poster", json={"kind": "art", "image": url}).status_code == 422
    bad = pc.put("/api/timelines/t1/poster",
                 json={"kind": "frame", "image": base64.b64encode(b"nope").decode()})
    assert bad.status_code == 400
    assert pc.put("/api/timelines/t1/poster", json={"kind": "frame", "image": "%%"}).status_code == 400

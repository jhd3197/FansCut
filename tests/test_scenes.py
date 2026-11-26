"""Scene tagging: cut tags, the shared tag list, and the API over the embedded Marcarr.

The analysis itself (subtitles, scene grouping, the AI pass, faces, signals) is
Marcarr's and tested there; these tests check FansCut's side of it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from fanscut.scenes import link
from fanscut.scenes import store as st
from fanscut.server import create_app
from tests.conftest import needs_ffmpeg, needs_prompture_video

SRT = """1
00:00:01,000 --> 00:00:03,500
<i>Happy Christmas, Harry.</i>

2
00:00:04,000 --> 00:00:06,000
- You've got presents!
- Me?

3
00:00:21,000 --> 00:00:24,000
{\\an8}Quidditch practice tonight.

4
00:00:25,000 --> 00:00:27,000
[door slams]
"""


# -- tags --------------------------------------------------------------------------
def test_tag_cleaning():
    assert st.clean_tags(["Christmas", "Great Hall", "christmas"]) == ["christmas", "great-hall"]
    with pytest.raises(Exception, match="at least one"):
        st.clean_tags([])
    assert st.clean_people(["Harry", {"name": "Rupert Grint", "character": "Ron"}, {}]) == \
        [{"character": "Harry"}, {"name": "Rupert Grint", "character": "Ron"}]
    with pytest.raises(Exception, match="after start"):
        st.clean_range(10, 5)
    with pytest.raises(Exception, match="past the film"):
        st.clean_range(10, 500, duration=100)


def test_cut_tags_keep_their_prefix():
    assert st.norm_tag("fc:Filler") == "fc:filler"
    assert st.clean_tags(["fc:recap", "Snow"]) == ["fc:recap", "snow"]
    with pytest.raises(Exception, match="bad tag"):
        st.norm_tag("fc:")


def test_vocab_comes_from_marcarr():
    from fanscut.config import FAMILIES
    from fanscut.scenes import vocab

    data = vocab.load()
    assert data["version"] >= 2 and len(data["tags"]) >= 130
    assert list(FAMILIES) == vocab.families()  # Settings validates against the same list
    assert {t["family"] for t in vocab.content_tags(["mood", "content"])} == \
        set(vocab.families()) - {"mood", "content"}
    assert all(vocab.is_smart(t["id"]) for t in vocab.cut_tags())
    assert vocab.name("fc:filler") == "Filler" and vocab.name("fc:made-up") == "Made up"
    signals = {t["id"] for t in vocab.public()["signals"]}
    assert {"no-face", "dialogue", "long-take"} <= signals  # measured: not tags
    assert {t["id"] for t in vocab.cut_tags("person")} == {"fc:filler", "fc:spoiler"}
    for t in [*data["tags"], *data["cut_tags"]]:  # every id is a valid stored tag
        assert st.norm_tag(t["id"]) == t["id"]


def test_store_round_trip(db):
    s = st.SceneStore(db)
    a = s.add_tag("radarr:1", 671, 10, 40, ["fc:filler"], [], "a detour", source="ai:x")
    s.add_tag("radarr:1", 671, 100, 120, ["fc:recap"], [], status="accepted")
    assert a["status"] == "proposed" and a["tags"] == ["fc:filler"]
    assert [t["start"] for t in s.tags(["radarr:1"], "fc:Filler")] == [10]
    assert s.overlapping("radarr:1", 30, 60, "fc:filler")["id"] == a["id"]
    assert s.overlapping("radarr:1", 50, 60, "fc:filler") is None
    s.update_tag(a["id"], {"status": "accepted", "start": 12, "tags": ["fc:filler", "fc:spoiler"]})
    assert s.tag(a["id"])["tags"] == ["fc:filler", "fc:spoiler"] and s.tag(a["id"])["start"] == 12
    s.delete_tag(a["id"])
    assert len(s.tags()) == 1


def test_tidy_accepts_sure_ai_moments_and_merges_twins(db):
    s = st.SceneStore(db)
    human = s.add_tag("radarr:1", 671, 100, 200, ["fc:filler", "fc:recap"], [{"name": "A"}],
                      source="user", status="accepted")
    twin = s.add_tag("radarr:1", 671, 120, 210, ["fc:filler", "fc:spoiler"], [{"name": "B"}],
                     source="ai:x", confidence=0.9, evidence=["a flashback"])
    sure = s.add_tag("radarr:1", 671, 400, 460, ["fc:intro"], [], source="ai:x", confidence=0.8)
    unsure = s.add_tag("radarr:1", 671, 500, 560, ["fc:outro"], [], source="ai:x", confidence=0.5)
    got = s.tidy("radarr:1", 0.75)
    assert got == {"auto_accepted": 2, "merged": 1}
    ids = {r["id"]: r for r in s.tags(["radarr:1"])}
    assert twin["id"] not in ids  # the person's moment stays and takes the AI's extras
    kept = ids[human["id"]]
    assert kept["tags"] == ["fc:filler", "fc:recap", "fc:spoiler"] and kept["evidence"] == ["a flashback"]
    assert {p["name"] for p in kept["people"]} == {"A", "B"}
    assert ids[sure["id"]]["status"] == "accepted" and ids[unsure["id"]]["status"] == "proposed"
    assert not st.same_moment({"start": 0, "end": 10, "tags": ["a"]}, {"start": 8, "end": 20, "tags": ["a"]})


# -- the link to Marcarr -----------------------------------------------------------
def test_films_become_marcarr_titles():
    assert link.title_id(SimpleNamespace(key="radarr:1", tmdb_id=671)) == "movie:tmdb:671"
    assert link.title_id(SimpleNamespace(key="foldermovie:Inception (2010)", tmdb_id=None)) == \
        "local:fanscut:foldermovie-inception-2010"


def test_marcarr_settings_follow_fanscut(cfg, tmp_path):
    cfg.ai.model = "ollama/qwen"
    cfg.ai.vision_model = "ollama/qwen-vl"
    cfg.scenes.families_off = ["content"]
    cfg.scenes.auto_accept = 0.8
    cfg.scenes.signals = False
    cfg.catalogues.tmdb_api_key = "k"
    m = link.marcarr_config(cfg)
    assert m.ai.enabled and m.ai.model == "ollama/qwen" and m.ai.vision_model == "ollama/qwen-vl"
    assert m.ai.auto_accept == 0.8 and m.tags.families_off == ["content"]
    assert m.analysis.steps["signals"] is False and m.analysis.on_new is False
    assert m.catalogues.tmdb_api_key == "k"
    assert m.data_dir == cfg.data_dir / "marcarr"
    cfg.marcarr.data_dir = str(tmp_path / "elsewhere")
    assert link.marcarr_config(cfg).data_dir == tmp_path / "elsewhere"


def test_vocab_api_and_cut_tag_counts(cfg, tmp_path):
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        v = c.get("/api/scenes/vocab").json()
        assert v["version"] >= 2 and len(v["families"]) == 11
        assert {t["detect"] for t in v["cut_tags"]} == {"auto", "ai", "person"}
        store = c.app.state.core.scenes.store
        store.add_tag("radarr:1", 671, 10, 40, ["fc:filler"], [], source="user")
        counts = {t["tag"]: t for t in c.get("/api/scenes/tag-counts").json()["tags"]}
        assert counts["fc:filler"]["smart"] and counts["fc:filler"]["name"] == "Filler"


# -- end to end: a generated film with a sidecar SRT -------------------------------
def _film(path: Path) -> None:
    argv = ["ffmpeg", "-v", "error", "-y"]
    for c in ("red", "lime", "blue"):
        argv += ["-f", "lavfi", "-i", f"color=c={c}:s=320x180:r=12:d=10"]
    argv += ["-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-filter_complex",
             "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)]
    subprocess.run(argv, check=True)


@pytest.fixture(scope="module")
def film_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("scenefilm") / "movies" / "Snow Film (2001) {tmdb-671}"
    d.mkdir(parents=True)
    _film(d / "film.mkv")
    (d / "film.en.srt").write_text(SRT, encoding="utf-8")
    return d.parent


@pytest.fixture
def sc(cfg, tmp_path, film_dir, monkeypatch):
    cfg.library.movies.path = str(film_dir)
    cfg.catalogues.offline = True
    cfg.scenes.signals = False  # measuring needs no model, but takes a while; its own check below
    monkeypatch.setattr("fanscut.browse._collections_of", lambda m: ["Snow Saga"])
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        film = c.get("/api/scenes/films").json()["films"][0]
        assert film["franchises"] == ["Snow Saga"] and film["indexed"] is False
        c.key = film["key"]
        c.run = c.core.scenes.marcarr.worker.run_pending  # Marcarr's jobs, by hand
        yield c


def _fake_ai(monkeypatch, first_end=10.0):
    """Marcarr's dialogue pass finds Christmas in the first scene; the look confirms it."""
    from marcarr.stages import label

    def read(cfg, doc, title, tags, cast, cancel=None, progress=None):
        return [{"start": 0.0, "end": first_end, "tags": ["christmas"], "people": ["Harry"],
                 "why": "presents", "confidence": 0.6}]

    def look(cfg, sheet, title, cand, tags, cast, signals=None):
        return SimpleNamespace(match=True, tags=["christmas"], seen=["wrapped presents"], people=["Harry"],
                               summary="Presents by the tree", confidence=0.6, first_tile=1, last_tile=6)

    monkeypatch.setattr(label, "read", read)
    monkeypatch.setattr(label, "look", look)
    monkeypatch.setattr("marcarr.stages.tagger.text_pass", lambda *a, **k: [])


@needs_ffmpeg
@needs_prompture_video
def test_scene_api_end_to_end(sc, monkeypatch):
    key = sc.key
    assert sc.get("/api/scenes/film", params={"key": key}).json()["index"] is None
    assert sc.post("/api/scenes/ai", json={"key": key, "tag": "christmas"}).status_code == 400  # no index

    job = sc.post("/api/scenes/index", json={"key": key}).json()
    assert job["kind"] == "marcarr" and job["steps"][:3] == ["shots", "subtitles", "scenes"]
    sc.run()
    res = sc.get(f"/api/jobs/{job['id']}").json()  # Marcarr's job, through FansCut's route
    assert res["status"] == "done", res
    doc = sc.get("/api/scenes/film", params={"key": key}).json()["index"]
    assert [round(a) for a, _b in doc["shots"]] == [0, 10, 20]
    assert doc["scenes"][0]["start"] == 0 and doc["subtitles"]["lines"] == 4
    assert sc.get("/api/scenes/films").json()["films"][0]["indexed"] is True

    lines = sc.get("/api/scenes/transcript", params={"key": key, "start": 0, "end": 10}).json()["lines"]
    assert [x["text"][:5] for x in lines] == ["Happy", "- You"]
    said = sc.get("/api/scenes/transcript", params={"key": key, "start": 20, "speech": "true"}).json()["lines"]
    assert [x["text"] for x in said] == ["Quidditch practice tonight."]  # the door slam is not speech
    r = sc.get("/api/scenes/sheet", params={"key": key, "start": 0, "end": 30, "cols": 3, "rows": 1})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert r.headers["x-tile-times"] == "5.00,15.00,25.00"
    assert sc.get("/api/scenes/frame", params={"key": key, "t": 12}).content[:2] == b"\xff\xd8"
    assert sc.get("/api/scenes/clip", params={"key": key, "start": 0, "end": 4}).content[4:8] == b"ftyp"

    # A person's tag is kept, lands in Marcarr and snaps to the camera cut at 10 s.
    t = sc.post("/api/scenes/tags", json={"key": key, "start": 0, "end": 9.2, "tags": ["Christmas"],
                                          "people": ["Harry"], "summary": "presents"}).json()
    assert (t["status"], t["tags"], t["source"]) == ("accepted", ["christmas"], "user")
    assert t["end"] == pytest.approx(10.0, abs=0.1)
    assert t["id"].startswith("tag_")
    assert sc.core.scenes.marcarr.store.tag(t["id"])["source"] == "person"
    # a cut tag stays in FansCut
    fc = sc.post("/api/scenes/tags", json={"key": key, "start": 20, "end": 30, "tags": ["fc:filler"]}).json()
    assert not fc["id"].startswith("tag_")

    # The cast (Plex or TMDB in real use) is Marcarr's.
    sc.core.scenes.marcarr.store.save_cast("movie:tmdb:671", "test", [
        {"name": "Daniel Radcliffe", "character": "Harry Potter", "photo": "h.jpg"}])
    cast = sc.get("/api/scenes/cast", params={"key": key}).json()["cast"]
    assert cast == [{"name": "Daniel Radcliffe", "character": "Harry Potter", "photo": "h.jpg",
                     "id": "harry-potter"}]

    # The AI: Marcarr's tags step, with its model calls faked.
    sc.core.cfg.ai.model = "ollama/test"
    sc.core.scenes.link.apply_config(sc.core.cfg)
    _fake_ai(monkeypatch)
    sc.core.scenes.marcarr.store.delete_tag(t["id"])  # so the AI's christmas is not a duplicate
    job = sc.post("/api/scenes/label", json={"key": key}).json()
    sc.run()
    assert sc.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    tags = sc.get("/api/scenes/tags", params={"key": key, "tag": "christmas"}).json()["tags"]
    assert len(tags) == 1 and tags[0]["status"] == "proposed"
    assert tags[0]["source"] == "ai:ollama/test" and tags[0]["summary"] == "Presents by the tree"
    assert tags[0]["evidence"][0] == "wrapped presents"
    assert tags[0]["people"] == [{"name": "Daniel Radcliffe", "character": "Harry Potter"}]

    kept = sc.patch(f"/api/scenes/tags/{tags[0]['id']}", json={"status": "accepted"}).json()
    assert kept["status"] == "accepted" and kept["film_key"] == key
    xr = sc.get("/api/scenes/xray", params={"key": key, "t": 5}).json()
    assert xr["tags"][0]["tags"] == ["christmas"]
    assert xr["people"][0]["character"] == "Harry Potter" and xr["people"][0]["photo"] == "h.jpg"
    faces = sc.get("/api/scenes/faces", params={"key": key, "t": 5}).json()
    assert faces["faces"] == [] and faces["width"] == 320  # no face models in tests

    col = sc.get("/api/scenes/collection", params={"name": "Snow Saga"}).json()
    assert {x["tag"] for x in col["tags"]} == {"christmas", "fc:filler"}
    tl = sc.post("/api/scenes/to-timeline", json={"collection": "Snow Saga", "tag": "christmas"})
    assert tl.status_code == 201, tl.text
    seg = sc.core.get_timeline(tl.json()["id"])["doc"]["parts"][0]["segments"]
    assert [(s["src"], s["start"]) for s in seg] == [("M671", 0.0)]
    assert sc.post("/api/scenes/to-timeline", json={"collection": "Snow Saga", "tag": "feast"}).status_code == 400
    unknown = sc.post("/api/scenes/to-timeline", json={"collection": "x", "tag": "christmas",
                                                       "films": ["nope:1"]})
    assert unknown.status_code == 404

    gone = [sc.delete(f"/api/scenes/tags/{t['id']}").status_code for t in (kept, fc)]
    assert gone == [204, 204]
    assert sc.get("/api/scenes/tags", params={"key": key}).json()["tags"] == []


@needs_ffmpeg
@needs_prompture_video
def test_signals_are_measured_by_marcarr(sc):
    key = sc.key
    sc.core.cfg.scenes.signals = True
    sc.core.scenes.link.apply_config(sc.core.cfg)
    assert sc.get("/api/scenes/signals", params={"key": key}).status_code == 404
    assert sc.post("/api/scenes/signals", json={"key": key}).status_code == 400  # needs the index
    sc.post("/api/scenes/index", json={"key": key})
    sc.run()
    sc.post("/api/scenes/signals", json={"key": key})
    sc.run()
    sig = sc.get("/api/scenes/signals", params={"key": key}).json()
    assert sig["key"] == key and sig["signals"]["still"]  # three flat colour shots barely move
    assert sc.core.scenes.signals_of(key)["signals"]["still"]

"""Scene tagging: cut tags, the shared tag list, and the API over Marcarr's.

The analysis itself (subtitles, scene grouping, the AI pass, faces, signals) is
Marcarr's and tested there. These tests run a real Marcarr app in-process and point
FansCut's link at it, so every call goes through Marcarr's HTTP API.
"""

from __future__ import annotations

import json
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


@pytest.fixture
def marcarr_list(tmp_path):
    """The tag list read from a Marcarr (no film needed), kept in a file like the app does."""
    from marcarr.config import Config as MarcarrConfig
    from marcarr.core import Core as MarcarrCore
    from marcarr.server import create_app as marcarr_app

    from fanscut.scenes import vocab

    mcfg = MarcarrConfig.model_validate({"server": {"data_dir": str(tmp_path / "marcarr")},
                                         "setup_done": True})
    m = MarcarrCore(mcfg, tmp_path / "marcarr.yaml", start_worker=False)
    http = TestClient(marcarr_app(m))
    vocab.use(lambda: http.get("/api/v1/tags/vocabulary").json(), lambda: tmp_path / "tags.json")
    yield http
    vocab.use(None)
    m.close()


def test_vocab_comes_from_marcarr(marcarr_list, tmp_path):
    from fanscut.config import FAMILIES
    from fanscut.scenes import vocab

    data = vocab.load()
    assert data["version"] >= 2 and len(data["tags"]) >= 130
    assert (tmp_path / "tags.json").is_file()  # kept for when Marcarr does not answer
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
    # a custom tag made in Marcarr ("My tags") is named here too, once the list is read again
    assert vocab.get("wand-duel") is None and vocab.name("wand-duel") == "Wand duel"
    marcarr_list.post("/api/v1/tags/mine", json={"name": "Wand Duel!", "id": "wand-duel", "family": "action"})
    vocab.forget()
    assert vocab.name("wand-duel") == "Wand Duel!" and vocab.public()["mine"][0]["family"] == "action"


def test_vocab_without_marcarr(tmp_path):
    from fanscut.scenes import vocab

    def down():
        raise OSError("no Marcarr")

    try:
        vocab.use(down, lambda: tmp_path / "tags.json")
        assert vocab.load()["tags"] == [] and vocab.families() == []  # never read: empty
        assert vocab.name("fc:filler") == "Filler" and vocab.get("christmas") is None
        saved = {"version": 2, "families": ["occasion"], "cut_tags": [],
                 "tags": [{"id": "christmas", "name": "Christmas", "family": "occasion"}]}
        (tmp_path / "tags.json").write_text(json.dumps(saved), encoding="utf-8")
        vocab.forget()
        assert vocab.name("christmas") == "Christmas"  # the last list read is used
    finally:
        vocab.use(None)


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


def test_the_link_reports_what_marcarr_says(cfg):
    import requests

    from fanscut.errors import ClientError, FansCutError, NotFoundError

    class Reply:
        def __init__(self, status, body=None):
            self.status_code, self._body = status, body
            self.content = b"x" if body is not None else b""
            self.text = ""

        def json(self):
            return self._body

    sent = []

    class Http:
        def __init__(self, reply):
            self.reply = reply

        def request(self, method, path, **kw):
            sent.append((method, path, kw))
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply

    from fanscut.config import Config

    assert Config().marcarr.url == "http://127.0.0.1:7470"
    cfg.marcarr.api_key = "mk_test"
    ln = link.Link(lambda: cfg)
    ln.use(Http(Reply(200, {"ok": 1})))
    assert ln.get("/status", skip=None, x=1) == {"ok": 1}
    method, path, kw = sent[-1]
    assert (method, path, kw["params"]) == ("GET", "/status", {"x": 1})
    assert kw["headers"] == {"X-Marcarr-Client": "fanscut", "X-Api-Key": "mk_test"}
    for status, err in ((404, NotFoundError), (403, ClientError), (500, ClientError), (400, FansCutError)):
        ln.use(Http(Reply(status, {"detail": "nope"})))
        with pytest.raises(err):
            ln.get("/x")
    ln.use(Http(requests.ConnectionError("refused")))
    with pytest.raises(ClientError, match="did not answer"):
        ln.get("/x")


def test_vocab_api_and_cut_tag_counts(cfg, tmp_path):
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        v = c.get("/api/scenes/vocab").json()
        assert v["version"] == 0 and v["tags"] == []  # no Marcarr set up: no list yet
        store = c.app.state.core.scenes.store  # a cut tag made before Marcarr kept them
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
def marcarr(tmp_path, film_dir):
    """A Marcarr with the test film, as if it had scanned it from Plex."""
    from marcarr.config import Config as MarcarrConfig
    from marcarr.core import Core as MarcarrCore

    mcfg = MarcarrConfig.model_validate({"server": {"data_dir": str(tmp_path / "marcarr")},
                                         "setup_done": True})
    m = MarcarrCore(mcfg, tmp_path / "marcarr.yaml", start_worker=False)
    m.store.upsert_title({"id": "movie:tmdb:671", "type": "movie", "name": "Snow Film",
                          "sort_name": "snow film", "year": 2001, "tmdb_id": 671})
    m.library.add_file("movie:tmdb:671", str(film_dir / "Snow Film (2001) {tmdb-671}" / "film.mkv"))
    yield m
    m.close()


def marcarr_ai(m) -> None:
    """Turn Marcarr's AI on (its tags step is off without a model)."""
    m.cfg = m.cfg.model_copy(update={"ai": m.cfg.ai.model_copy(update={"enabled": True, "model": "ollama/test"})})
    m.forget_caps()


@pytest.fixture
def sc(cfg, tmp_path, film_dir, marcarr, monkeypatch):
    from marcarr.server import create_app as marcarr_app

    cfg.library.movies.path = str(film_dir)
    cfg.catalogues.offline = True
    cfg.scenes.signals = False  # measuring needs no model, but takes a while; its own check below
    cfg.marcarr.url = "http://marcarr.test"  # where photos are linked; calls go to the app below
    monkeypatch.setattr("fanscut.browse._collections_of", lambda m: ["Snow Saga"])
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        c.core.scenes.link.use(TestClient(marcarr_app(marcarr), base_url="http://marcarr/api/v1"))
        film = c.get("/api/scenes/films").json()["films"][0]
        assert film["franchises"] == ["Snow Saga"] and film["indexed"] is False
        assert film["in_marcarr"] is True
        c.key = film["key"]
        c.m = marcarr
        c.run = marcarr.worker.run_pending  # Marcarr's jobs, by hand
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
    assert sc.m.store.tag(t["id"])["source"] == "fanscut"  # Marcarr knows it came from FansCut
    # a cut tag is kept in Marcarr too, at the exact times given
    fc = sc.post("/api/scenes/tags", json={"key": key, "start": 20.4, "end": 29.5, "tags": ["fc:filler"]}).json()
    assert fc["id"].startswith("tag_") and (fc["start"], fc["end"]) == (20.4, 29.5)
    assert (fc["status"], fc["tags"], fc["source"]) == ("accepted", ["fc:filler"], "user")
    assert sc.m.store.tag(fc["id"])["tag"] == "fc:filler"
    assert sc.core.scenes.store.tags() == []  # nothing new is written to FansCut's own table
    names = sc.get("/api/scenes/vocab").json()
    assert {t["id"] for t in names["cut_tags"]} >= {"fc:filler", "fc:spoiler"} and len(names["families"]) == 11
    both = sc.post("/api/scenes/tags", json={"key": key, "start": 12, "end": 18,
                                             "tags": ["snow", "fc:spoiler"], "status": "proposed"}).json()
    assert both["tags"] == ["snow", "fc:spoiler"] and len(both["ids"]) == 2
    for tid in both["ids"]:
        assert sc.delete(f"/api/scenes/tags/{tid}").status_code == 204

    # The cast (Plex or TMDB in real use) is Marcarr's.
    sc.m.store.save_cast("movie:tmdb:671", "test", [
        {"name": "Daniel Radcliffe", "character": "Harry Potter", "photo": "h.jpg"}])
    cast = sc.get("/api/scenes/cast", params={"key": key}).json()["cast"]
    photo = "http://marcarr.test/api/v1/people/harry-potter/photo"  # Marcarr serves it
    assert cast == [{"name": "Daniel Radcliffe", "character": "Harry Potter", "photo": photo,
                     "id": "harry-potter"}]

    # The AI: Marcarr's tags step, with its model calls faked.
    marcarr_ai(sc.m)
    _fake_ai(monkeypatch)
    sc.m.store.delete_tag(t["id"])  # so the AI's christmas is not a duplicate
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
    assert xr["people"][0]["character"] == "Harry Potter" and xr["people"][0]["photo"] == photo
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
def test_cut_tags_made_before_move_to_marcarr(sc):
    key = sc.key
    store = sc.core.scenes.store
    old = store.add_tag(key, 671, 3.5, 8.25, ["fc:filler", "fc:recap"], [{"character": "Harry"}],
                        "a detour", source="user", status="accepted", evidence=["a flashback"])
    store.add_tag(key, 671, 21, 27, ["fc:spoiler"], [], source="ai:x", status="rejected", confidence=0.4)
    store.add_tag("radarr:999", 1, 0, 5, ["fc:filler"], [], status="accepted")  # a film that is gone

    dry = sc.post("/api/scenes/tags/move", json={"dry_run": True}).json()
    assert (dry["moved"], len(dry["skipped"]), dry["dry_run"]) == (2, 1, True)
    assert sc.m.store.tags() == [] and len(store.tags()) == 3  # nothing sent, nothing marked

    got = sc.post("/api/scenes/tags/move", json={}).json()
    assert got["moved"] == 2 and got["moved_before"] == 0
    assert got["skipped"] == [{"id": got["skipped"][0]["id"], "film_key": "radarr:999",
                               "tags": ["fc:filler"], "why": "the film is not in the library"}]
    there = {r["tag"]: r for r in sc.m.store.tags()}
    assert set(there) == {"fc:filler", "fc:recap", "fc:spoiler"}
    assert (there["fc:filler"]["start"], there["fc:filler"]["end"]) == (3.5, 8.25)
    assert there["fc:filler"]["status"] == "accepted" and there["fc:spoiler"]["status"] == "rejected"
    assert there["fc:filler"]["summary"] == "a detour" and there["fc:filler"]["source"] == "fanscut"
    assert there["fc:filler"]["evidence"]["seen"] == ["a flashback"]
    # the rows moved stay in FansCut's database, marked; only the skipped one is still listed
    assert [r["film_key"] for r in store.tags()] == ["radarr:999"]
    assert store.tag(old["id"])["moved_at"] and store.moved() == 2
    again = sc.post("/api/scenes/tags/move", json={}).json()
    assert again["moved"] == 0 and again["moved_before"] == 2 and len(again["skipped"]) == 1
    assert len(sc.m.store.tags()) == 3  # nothing is copied twice


@needs_ffmpeg
@needs_prompture_video
def test_signals_are_measured_by_marcarr(sc):
    key = sc.key
    assert sc.get("/api/scenes/signals", params={"key": key}).status_code == 404
    assert sc.post("/api/scenes/signals", json={"key": key}).status_code == 400  # needs the index
    sc.post("/api/scenes/index", json={"key": key})
    sc.run()
    sc.post("/api/scenes/signals", json={"key": key})
    sc.run()
    sig = sc.get("/api/scenes/signals", params={"key": key}).json()
    assert sig["key"] == key and sig["signals"]["still"]  # three flat colour shots barely move
    assert sc.core.scenes.signals_of(key)["signals"]["still"]

"""Scene tagging: subtitles, scene grouping, tags, cast, the AI pass, the API."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fanscut import ai
from fanscut.scenes import cast as cast_mod
from fanscut.scenes import index as idx
from fanscut.scenes import store as st
from fanscut.scenes import subs, tagger
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
"""

CAST = [{"name": "Daniel Radcliffe", "character": "Harry Potter", "photo": "h.jpg"},
        {"name": "Rupert Grint", "character": "Ron Weasley", "photo": "r.jpg"},
        {"name": "Richard Harris", "character": "Albus Dumbledore", "photo": ""}]


# -- subtitles ---------------------------------------------------------------------
def test_parse_srt_strips_tags_and_joins_rows():
    lines = subs.parse_srt(SRT)
    assert [(ln.start, ln.end) for ln in lines] == [(1.0, 3.5), (4.0, 6.0), (21.0, 24.0)]
    assert lines[0].text == "Happy Christmas, Harry."
    assert lines[1].text == "- You've got presents! - Me?"
    assert lines[2].text == "Quidditch practice tonight."


def test_sidecars_prefer_the_language_and_skip_forced(tmp_path):
    (tmp_path / "film.mkv").write_bytes(b"")
    for n in ("film.en.forced.srt", "film.fr.srt", "film.en.srt", "other.en.srt"):
        (tmp_path / n).write_text(SRT)
    assert [p.name for p in subs.sidecars(str(tmp_path / "film.mkv"))] == \
        ["film.en.srt", "film.en.forced.srt", "film.fr.srt"]


def test_pick_stream_prefers_full_dialogue_in_the_language():
    streams = [{"index": 0, "language": "en", "title": "Forced", "forced": True},
               {"index": 1, "language": "es", "title": "", "forced": False},
               {"index": 2, "language": "en", "title": "SDH", "forced": False},
               {"index": 3, "language": "en", "title": "English", "forced": False}]
    assert subs.pick_stream(streams)["index"] == 3
    assert subs.pick_stream(streams, "es")["index"] == 1
    assert subs.pick_stream([]) is None


@needs_ffmpeg
def test_subtitles_borrowed_from_a_same_length_copy(tmp_path):
    def clip(path, seconds):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=red:s=64x48:r=5:d={seconds}", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(path)], check=True)

    clip(tmp_path / "film.mp4", 6)
    clip(tmp_path / "Film (2001).mkv", 6)
    (tmp_path / "Film (2001).en.srt").write_text(SRT, encoding="utf-8")
    lines, src = subs.load(str(tmp_path / "film.mp4"))
    assert len(lines) == 3 and src["kind"] == "sidecar (from Film (2001).mkv)"

    clip(tmp_path / "Film (2001).mkv", 9)  # a different cut lends nothing
    assert subs.load(str(tmp_path / "film.mp4")) == ([], {"kind": "none"})


# -- scenes ------------------------------------------------------------------------
def test_group_scenes_cuts_only_where_nobody_talks():
    shots = [(0, 10), (10, 22), (22, 30), (30, 45), (45, 50)]
    lines = [subs.Line(21.0, 23.0, "talking across the 22s cut")]
    scenes = idx.group_scenes(shots, lines, min_len=20)
    # 10s: too short; 22s: someone talks across it; 30s: quiet and long enough
    assert [(s["start"], s["end"], s["shots"]) for s in scenes] == [(0, 30, 3), (30, 50, 2)]


def test_group_scenes_forces_a_cut_past_max_len():
    shots = [(float(i), float(i + 1)) for i in range(0, 100)]
    lines = [subs.Line(0, 100, "nonstop")]
    scenes = idx.group_scenes(shots, lines, max_len=30)
    assert all(s["end"] - s["start"] <= 30 for s in scenes)
    assert scenes[-1]["end"] == 100


def test_snap_moves_to_the_nearest_cut_within_reach():
    doc = {"shots": [[0, 10.2], [10.2, 30]]}
    assert idx.snap(doc, 9.0) == 10.2
    assert idx.snap(doc, 20.0) == 20.0


def test_edges_wait_for_the_dialogue():
    doc = {"shots": [[0, 10], [10, 20], [20, 30], [30, 60]],
           "lines": [{"start": 18.0, "end": 22.0, "text": "a line across the 20 s cut"},
                     {"start": 9.0, "end": 11.0, "text": "a line across the 10 s cut"}]}
    assert idx.quiet_end(doc, 19.0) == 30  # 20 s has someone talking; 30 s is quiet
    # nothing quiet in reach: the pause after the line ends (22.3), not mid-line at 20
    assert idx.quiet_end(doc, 19.0, reach=5) == 22.3
    talky = {"shots": [[0, 10], [10, 40]],
             "lines": [{"start": 5, "end": 12, "text": "a"}, {"start": 12, "end": 16, "text": "b"},
                       {"start": 18, "end": 25, "text": "c"}]}
    assert idx.quiet_end(talky, 9.0, reach=10) == 16.3  # after "b", in the 16-18 s pause
    assert idx.quiet_start(talky, 20.0, reach=10) == 17.7  # before "c"
    assert idx.quiet_end({"shots": [[0, 10], [10, 30]], "lines": [{"start": 0, "end": 30, "text": "x"}]},
                         9.0) == 10  # no pause at all: nearest cut
    assert idx.quiet_start(doc, 19.0) == 0  # 20 and 10 are talked across
    assert idx.quiet_end(doc, 29.0) == 30  # a quiet cut in snapping distance wins
    assert idx.quiet_start(doc, 35.0) == 30


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


def test_smart_tags_keep_their_prefix():
    assert st.norm_tag("fc:No Face") == "fc:no-face"
    assert st.clean_tags(["fc:close-up", "Snow"]) == ["fc:close-up", "snow"]
    with pytest.raises(Exception, match="bad tag"):
        st.norm_tag("fc:")


def test_vocab_ships_with_the_package():
    from fanscut.scenes import vocab

    data = vocab.load()
    assert data["version"] == 1 and len(data["tags"]) == 128
    assert len(vocab.families()) == 9
    assert {t["family"] for t in vocab.content_tags(["mood", "content"])} ==         set(vocab.families()) - {"mood", "content"}
    assert all(vocab.is_smart(t["id"]) for t in vocab.cut_tags())
    assert vocab.name("fc:no-face") == "No face" and vocab.name("fc:made-up") == "Made up"
    assert vocab.name("great-hall") == "Great hall"
    assert {t["id"] for t in vocab.cut_tags("person")} == {"fc:filler", "fc:spoiler"}
    # every id is a valid stored tag
    for t in [*data["tags"], *data["cut_tags"]]:
        assert st.norm_tag(t["id"]) == t["id"]


def test_store_round_trip(db):
    s = st.SceneStore(db)
    a = s.add_tag("radarr:1", 671, 10, 40, ["christmas"], [], "presents", source="ai:x")
    s.add_tag("radarr:1", 671, 100, 120, ["quidditch"], [], status="accepted")
    assert a["status"] == "proposed" and a["tags"] == ["christmas"]
    assert [t["start"] for t in s.tags(["radarr:1"], "Christmas")] == [10]
    assert s.overlapping("radarr:1", 30, 60, "christmas")["id"] == a["id"]
    assert s.overlapping("radarr:1", 50, 60, "christmas") is None
    s.update_tag(a["id"], {"status": "accepted", "start": 12, "tags": ["Christmas", "Feast"]})
    assert s.tag(a["id"])["tags"] == ["christmas", "feast"] and s.tag(a["id"])["start"] == 12
    assert {c["tag"]: c["accepted"] for c in s.tag_counts()} == {"christmas": 1, "feast": 1, "quidditch": 1}
    s.delete_tag(a["id"])
    assert len(s.tags()) == 1


# -- cast --------------------------------------------------------------------------
def test_cast_from_plex_and_tmdb():
    plex = {"Role": [{"tag": "Daniel Radcliffe", "role": "Harry Potter", "thumb": "http://x/1.jpg"}, {"role": "?"}]}
    assert cast_mod.from_plex(plex) == [{"name": "Daniel Radcliffe", "character": "Harry Potter",
                                         "photo": "http://x/1.jpg", "order": 0}]
    tmdb = {"cast": [{"name": "B", "character": "Ron", "order": 1, "id": 2, "profile_path": None},
                     {"name": "A", "character": "Harry", "order": 0, "id": 1, "profile_path": "/a.jpg"}]}
    out = cast_mod.from_tmdb(tmdb)
    assert [c["name"] for c in out] == ["A", "B"]
    assert out[0]["photo"].endswith("/w185/a.jpg") and out[1]["photo"] == ""


def test_people_resolve_to_the_cast():
    assert cast_mod.match_person(CAST, "Harry")["name"] == "Daniel Radcliffe"
    assert cast_mod.match_person(CAST, "Professor Dumbledore")["name"] == "Richard Harris"
    assert cast_mod.match_person(CAST, "Rupert Grint")["character"] == "Ron Weasley"
    assert cast_mod.match_person(CAST, "Hagrid") is None
    people = cast_mod.resolve_people(CAST, [{"character": "Harry"}, {"character": "harry potter"},
                                            {"character": "Hagrid"}])
    assert people == [{"name": "Daniel Radcliffe", "character": "Harry Potter"}, {"character": "Hagrid"}]


# -- the AI pass, pure parts -------------------------------------------------------
def _doc():
    scenes = [{"id": f"s{i}", "start": i * 100.0, "end": (i + 1) * 100.0, "shots": 3} for i in range(7)]
    lines = [{"start": 105, "end": 108, "text": "Happy Christmas!"},
             {"start": 450, "end": 452, "text": "Catch the snitch!"}]
    return {"scenes": scenes, "lines": lines, "shots": [[s["start"], s["end"]] for s in scenes]}


def test_windows_and_text():
    doc = _doc()
    assert tagger.windows(doc, 300) == [(0.0, 300.0), (300.0, 600.0), (600.0, 700.0)]
    assert tagger.window_text(doc, (0, 300)) == "[0:01:45] Happy Christmas!"
    assert tagger.batches(doc, tagger.windows(doc, 300)) == [[0, 1, 2]]


def test_text_pass_turns_hits_into_ranges(monkeypatch, cfg):
    cfg.ai.model = "ollama/x"
    seen = {}

    def fake(cfg_, schema, system, prompt, **kw):
        seen["prompt"] = prompt
        return tagger.WindowHits(matches=[
            tagger.WindowHit(window=0, start="1:40", end="3:00", why="presents", people=["Harry"],
                             confidence=0.9),
            tagger.WindowHit(window=9, start="0:10", end="0:20")])  # unknown window: ignored

    monkeypatch.setattr(ai, "ask_json", fake)
    out = tagger.text_pass(cfg.ai, _doc(), "HP1", "christmas", "Christmas at Hogwarts", CAST)
    assert out == [{"start": 100.0, "end": 180.0, "why": "presents", "people": ["Harry"], "confidence": 0.9}]
    assert "Window 0" in seen["prompt"] and "Harry Potter (Daniel Radcliffe)" in seen["prompt"]


def test_refine_merge_and_sweep():
    c = {"start": 0.0, "end": 60.0}
    check = tagger.SheetCheck(match=True, first_tile=2, last_tile=3)
    times = [5.0, 15.0, 25.0, 35.0, 45.0, 55.0]
    assert tagger.refine(c, check, times) == (10.0, 30.0)
    merged = tagger.merge([{"start": 0, "end": 10, "people": ["A"], "confidence": 0.2, "why": ""},
                           {"start": 15, "end": 30, "people": ["B"], "confidence": 0.8, "why": "x"},
                           {"start": 100, "end": 110, "people": [], "confidence": 0.1, "why": ""}])
    assert [(m["start"], m["end"], m["people"], m["confidence"]) for m in merged] == \
        [(0, 30, ["A", "B"], 0.8), (100, 110, [], 0.1)]
    assert [s["start"] for s in tagger.sweep_candidates(_doc(), max_lines=0)] == [0.0, 200.0, 300.0, 500.0, 600.0]


def test_grid_grows_with_the_range():
    assert tagger.sheet_shape(45) == (3, 2, 240)
    assert tagger.sheet_shape(77) == (3, 3, 240)
    assert tagger.sheet_shape(180) == (3, 3, 240)
    cols, rows, h = tagger.sheet_shape(400)
    assert (cols, rows) == (4, 3) and cols * (h * 16 // 9) + 12 <= 1568


def test_edge_pass_times():
    times = [2.5, 7.5, 12.5, 17.5, 22.5, 27.5]  # 30 s window, 5 s apart
    assert tagger.edge_time(times, tagger.EdgeCheck(first_tile=3, last_tile=6), "start") == 10.0
    assert tagger.edge_time(times, tagger.EdgeCheck(first_tile=1, last_tile=4), "end") == 20.0
    assert tagger.edge_time(times, tagger.EdgeCheck(first_tile=5, last_tile=2), "end") is None
    assert tagger.edge_time(times, tagger.EdgeCheck(first_tile=0, last_tile=9), "start") is None
    assert tagger.edge_window(5, 100) == (0.0, 20.0)
    assert tagger.edge_window(95, 100) == (80.0, 100)


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
    monkeypatch.setattr("fanscut.browse._collections_of", lambda m: ["Snow Saga"])
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        c.core = c.app.state.core  # type: ignore[attr-defined]
        film = c.get("/api/scenes/films").json()["films"][0]
        assert film["franchises"] == ["Snow Saga"]
        c.key = film["key"]
        yield c


@needs_ffmpeg
@needs_prompture_video
def test_scene_api_end_to_end(sc, monkeypatch):
    key = sc.key
    sc.core.cfg.scenes.smart_tags = False  # measured tags have their own test
    assert sc.get("/api/scenes/film", params={"key": key}).json()["index"] is None
    assert sc.post("/api/scenes/ai", json={"key": key, "tag": "christmas"}).status_code == 400  # no index

    job = sc.post("/api/scenes/index", json={"key": key}).json()
    sc.core.worker.run_pending()
    res = sc.get(f"/api/jobs/{job['id']}").json()
    assert res["status"] == "done", res
    assert res["result"]["subtitles"] == "sidecar" and res["result"]["lines"] == 3
    doc = sc.get("/api/scenes/film", params={"key": key}).json()["index"]
    assert [round(a) for a, _b in doc["shots"]] == [0, 10, 20]
    assert doc["scenes"][0]["start"] == 0

    lines = sc.get("/api/scenes/transcript", params={"key": key, "start": 0, "end": 10}).json()["lines"]
    assert [x["text"][:5] for x in lines] == ["Happy", "- You"]
    r = sc.get("/api/scenes/sheet", params={"key": key, "start": 0, "end": 30, "cols": 3, "rows": 1})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert r.headers["x-tile-times"] == "5.00,15.00,25.00"
    assert sc.get("/api/scenes/frame", params={"key": key, "t": 12}).content[:2] == b"\xff\xd8"
    assert sc.get("/api/scenes/clip", params={"key": key, "start": 0, "end": 4}).content[4:8] == b"ftyp"

    # A person's tag is accepted and snaps to the camera cut at 10 s.
    t = sc.post("/api/scenes/tags", json={"key": key, "start": 0, "end": 9.2, "tags": ["Christmas"],
                                          "people": ["Harry"], "summary": "presents"}).json()
    assert (t["status"], t["tags"]) == ("accepted", ["christmas"])
    assert t["end"] == pytest.approx(10.0, abs=0.1)

    # The AI job: subtitles find 0:20-0:24 for quidditch, the look pass confirms tiles 2-3.
    sc.core.cfg.ai.model = "ollama/test"

    def fake(cfg_, schema, system, prompt, images=None, **kw):
        if schema is tagger.WindowHits:
            return tagger.WindowHits(matches=[tagger.WindowHit(window=0, start="0:20", end="0:29",
                                                               people=["Ron"], confidence=0.8)])
        assert images and images[0][:2] == b"\xff\xd8"
        if schema is tagger.EdgeCheck:
            # around the start (20 s) the blue shot begins at tile 4; at the end it runs out
            return tagger.EdgeCheck(first_tile=4 if "starts" in prompt else 1, last_tile=6)
        return tagger.SheetCheck(match=True, first_tile=1, last_tile=6, summary="practice", people=["Ron"],
                                 confidence=0.6)

    monkeypatch.setattr(ai, "ask_json", fake)
    job = sc.post("/api/scenes/ai", json={"key": key, "tag": "quidditch", "description": "a match"}).json()
    sc.core.worker.run_pending()
    res = sc.get(f"/api/jobs/{job['id']}").json()
    assert res["status"] == "done", res
    assert res["result"]["proposed"] == 1
    tags = sc.get("/api/scenes/tags", params={"key": key, "tag": "quidditch"}).json()["tags"]
    assert tags[0]["status"] == "proposed" and tags[0]["start"] == pytest.approx(20.0, abs=0.1)
    assert tags[0]["source"] == "ai:ollama/test" and tags[0]["summary"] == "practice"

    # Re-running finds the same range and proposes nothing new.
    sc.post("/api/scenes/ai", json={"key": key, "tag": "quidditch"})
    sc.core.worker.run_pending()
    assert len(sc.get("/api/scenes/tags", params={"key": key, "tag": "quidditch"}).json()["tags"]) == 1

    sc.patch(f"/api/scenes/tags/{tags[0]['id']}", json={"status": "accepted"})
    xr = sc.get("/api/scenes/xray", params={"key": key, "t": 5}).json()
    assert xr["tags"][0]["tags"] == ["christmas"] and xr["people"][0]["character"] == "Harry"

    col = sc.get("/api/scenes/collection", params={"name": "Snow Saga"}).json()
    assert {x["tag"] for x in col["tags"]} == {"christmas", "quidditch"}
    tl = sc.post("/api/scenes/to-timeline", json={"collection": "Snow Saga", "tag": "christmas"})
    assert tl.status_code == 201, tl.text
    seg = sc.core.get_timeline(tl.json()["id"])["doc"]["parts"][0]["segments"]
    assert [(s["src"], s["start"]) for s in seg] == [("M671", 0.0)]
    assert seg[0]["end"] == pytest.approx(10.0, abs=0.1)
    assert sc.post("/api/scenes/to-timeline", json={"collection": "Snow Saga", "tag": "feast"}).status_code == 400
    by_keys = sc.post("/api/scenes/to-timeline",
                      json={"collection": "Any name", "tag": "christmas", "films": [key]})
    assert by_keys.status_code == 201, by_keys.text
    unknown = sc.post("/api/scenes/to-timeline", json={"collection": "x", "tag": "christmas",
                                                       "films": ["nope:1"]})
    assert unknown.status_code == 404

    gone = sc.delete(f"/api/scenes/tags/{t['id']}")
    assert gone.status_code == 204


def test_vocab_api_and_smart_tag_counts(cfg, tmp_path):
    with TestClient(create_app(cfg, tmp_path / "config.yaml", start_worker=False)) as c:
        v = c.get("/api/scenes/vocab").json()
        assert v["version"] == 1 and len(v["families"]) == 9
        assert {t["detect"] for t in v["cut_tags"]} == {"auto", "ai", "person"}
        store = c.app.state.core.scenes.store
        store.add_tag("radarr:1", 671, 10, 40, ["christmas", "fc:no-face"], [], source="auto")
        counts = {t["tag"]: t for t in c.get("/api/scenes/tag-counts").json()["tags"]}
        assert counts["fc:no-face"]["smart"] and counts["fc:no-face"]["name"] == "No face"
        assert not counts["christmas"]["smart"] and counts["christmas"]["family"] == "occasion"


def test_face_names_need_a_looser_match_for_people_in_the_moment():
    from fanscut.scenes import faces

    scores = [(0.35, "Rupert Grint"), (0.33, "Daniel Radcliffe")]
    assert faces.pick(scores, set()) is None
    assert faces.pick(scores, {"Daniel Radcliffe"}) == ("Daniel Radcliffe", 0.33)
    assert faces.pick([(0.45, "Tom Felton")], set()) == ("Tom Felton", 0.45)

    class Engine:  # two faces: one matches Harry, one nobody
        def detect(self, img):
            return [[10, 20, 50, 60, *[0] * 10, 0.9], [200, 20, 40, 40, *[0] * 10, 0.8]]

        def feature(self, img, f):
            return f[0]

        def match(self, a, b):
            return 0.38 if a == 10 and b == "h" else 0.1

    class Img:
        shape = (100, 400, 3)

    refs = {"Daniel Radcliffe": {"character": "Harry Potter", "photo": "h.jpg", "feature": "h"}}
    got = faces.name_faces(Engine(), Img(), refs, {"Daniel Radcliffe"})
    assert got[0]["name"] == "Daniel Radcliffe" and got[0]["character"] == "Harry Potter"
    assert got[0]["sure"] is False and got[0]["match"] == 0.38  # below SURE: "Harry Potter?"
    assert got[0]["box"] == [0.025, 0.2, 0.125, 0.6]
    assert got[1]["name"] is None

    class Twins(Engine):  # both faces look like Hermione: only the closer one is her
        def match(self, a, b):
            return {10: 0.6, 200: 0.5}[a]

    refs = {"Emma Watson": {"character": "Hermione Granger", "photo": "", "feature": "e"}}
    got = faces.name_faces(Twins(), Img(), refs, set())
    assert [g["name"] for g in got] == ["Emma Watson", None]


def test_face_models_download_once(tmp_path):
    from fanscut.scenes import faces

    class Resp:
        content = b"onnx"

        def raise_for_status(self):
            pass

    calls = []

    class Session:
        def get(self, url, timeout):
            calls.append(url)
            return Resp()

    d, r = faces.ensure_models(tmp_path, Session())
    assert d.name == "face_detection_yunet_2023mar.onnx" and r.read_bytes() == b"onnx"
    faces.ensure_models(tmp_path, Session())
    assert len(calls) == 2


# -- smart tags (measured) --------------------------------------------------------
def test_smart_labels_from_numbers():
    import numpy as np

    from fanscut.scenes import smart

    shots = [[0, 10], [10, 11], [11, 12], [12, 13], [13, 14], [14, 15], [15, 16], [16, 40]]
    lines = [{"start": 16.0, "end": 19.0, "text": "Happy Christmas."},
             {"start": 20.0, "end": 24.0, "text": "Me?"},
             {"start": 30.0, "end": 31.0, "text": "[CAT MEOWS]"}]
    doc = {"duration": 40.0, "shots": shots, "lines": lines}
    flat = np.zeros((36, 64), np.float32)
    samples = []
    for t in range(40):
        if t < 10:  # a dark, still, empty opening
            samples.append(smart.Sample(t, 13.0, flat, 0, 0.0))
        elif t < 16:  # quick shots with a crowd
            samples.append(smart.Sample(t, 120.0, flat + t * 10, 4, 0.2))
        else:  # a long close-up
            samples.append(smart.Sample(t, 120.0, flat + (t % 2) * 40, 1, 0.5))
    loud = np.full(80, -30.0, np.float32)
    loud[0:10] = -70  # silent first 5 s
    loud[50] = -5  # one bang at 25 s
    got = smart.label(doc, samples, loud)
    assert got["fc:no-face"] == [(0.0, 10.0)]
    assert got["fc:dark"] == [(0.0, 10.0)] and got["fc:still"] == [(0.0, 10.0)]
    assert got["fc:group"] == [(10.0, 16.0)]
    assert got["fc:close-up"] == [(16.0, 40.0)]
    assert got["fc:long-take"] == [(16.0, 40.0)]
    assert got["fc:fast-cutting"] and got["fc:fast-cutting"][0][0] <= 11
    assert got["fc:dialogue"] == [(16.0, 24.0)]  # the cat isn't dialogue
    assert got["fc:silence"] == [(0.0, 5.0)]
    assert got["fc:loud"] == [(23.5, 27.0)]
    assert smart.speech("- Me?") and not smart.speech("♪ Hedwig's theme ♪")


@needs_ffmpeg
@needs_prompture_video
def test_smart_and_label_jobs(sc, monkeypatch):
    from fanscut.scenes import faces, label

    key = sc.key
    monkeypatch.setattr(faces, "available", lambda: False)  # no face models in tests
    sc.post("/api/scenes/index", json={"key": key})
    sc.core.worker.run_pending()  # indexing queues the smart pass (smart_tags on)
    sc.core.worker.run_pending()
    smart_rows = [t for t in sc.get("/api/scenes/tags", params={"key": key}).json()["tags"]
                  if t["source"] == "auto"]
    assert smart_rows and all(t["tags"][0].startswith("fc:") for t in smart_rows)
    # three flat colour shots of 10 s: each barely moves
    assert {t["tags"][0] for t in smart_rows} == {"fc:still"}
    # a re-run replaces the last one
    job = sc.post("/api/scenes/smart", json={"key": key}).json()
    sc.core.worker.run_pending()
    assert sc.get(f"/api/jobs/{job['id']}").json()["status"] == "done"
    again = [t for t in sc.get("/api/scenes/tags", params={"key": key}).json()["tags"]
             if t["source"] == "auto"]
    assert len(again) == len(smart_rows)

    assert sc.post("/api/scenes/label", json={"key": key}).status_code == 400  # no model
    sc.core.cfg.ai.model = "ollama/test"
    assert sc.post("/api/scenes/label", json={"key": key, "families": ["nope"]}).status_code == 400

    def fake(cfg_, schema, system, prompt, images=None, **kw):
        if schema.__name__ == "Moments":
            assert "christmas:" in prompt and "gore:" not in prompt  # content family off
            return schema(moments=[{"window": 0, "start": "0:01", "end": "0:09",
                                    "tags": ["christmas", "happy"], "people": ["Harry"],
                                    "why": "presents", "confidence": 0.8}])
        assert images and "christmas" in prompt
        return schema(tags=["christmas", "snow"], first_tile=1, last_tile=6, summary="presents",
                      people=["Harry"], confidence=0.6)

    monkeypatch.setattr(ai, "ask_json", fake)
    fams = [f for f in label.vocab.families() if f != "content"]
    job = sc.post("/api/scenes/label", json={"key": key, "families": fams}).json()
    sc.core.worker.run_pending()
    res = sc.get(f"/api/jobs/{job['id']}").json()
    assert res["status"] == "done", res
    assert res["result"]["proposed"] == 1 and res["result"]["tags"] == {"christmas": 1, "snow": 1}
    row = next(t for t in sc.get("/api/scenes/tags", params={"key": key}).json()["tags"]
               if t["source"] == "ai:ollama/test")
    assert row["tags"] == ["christmas", "snow"] and row["status"] == "proposed"
    # the schema refuses ids outside the list
    moments, _ = label.schemas(["christmas"])
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        moments.model_validate({"moments": [{"window": 0, "start": "0", "end": "1",
                                             "tags": ["quidditch"]}]})

"""Library check: one test per finding, modelled on the real Hobbit/LOTR/AoT files."""

from __future__ import annotations

from pathlib import Path

from fanscut.core import Core
from fanscut.db import Database
from fanscut.library import check, known
from fanscut.models import Episode, Series

HDR = {"codec": "hevc", "width": 3836, "height": 1608, "fps": 23.976, "hdr": "HDR10"}
ENG = [{"codec": "eac3", "language": "eng", "title": ""},
       {"codec": "ac3", "language": "eng", "title": "Commentary by director Peter Jackson"}]
RUS_FORCED = [{"language": "rus", "title": "forced", "forced": True},
              {"language": "eng", "title": "full", "forced": False}]
AUJ = known.movie(49051)
DOS = known.movie(57158)


def info(minutes: float, video=None, audio=None, subs=None) -> dict:
    return {"v": 2, "duration": minutes * 60, "video": video or HDR,
            "audio_tracks": ENG if audio is None else audio,
            "subtitle_tracks": RUS_FORCED if subs is None else subs}


def checks(found: list[dict]) -> list[tuple[str, str]]:
    return [(f["check"], f["severity"]) for f in found]


def test_edition_proved_by_runtime_when_filename_is_silent():
    # Smaug: 186.6 min, the filename did not say "extended"
    found, ed = check.check_movie_file("/m/The Hobbit - The Desolation of Smaug (2013).mkv",
                                       "The Hobbit - The Desolation of Smaug", 2013,
                                       info(186.6), DOS, "eng", "")
    assert ed == "Extended"
    msg = next(f for f in found if f["check"] == "edition")
    assert "187 min" in msg["message"] and "{edition-Extended}" in msg["fix"]


def test_filename_edition_contradicted_by_runtime():
    found, ed = check.check_movie_file("/m/Smaug (2013) {edition-Theatrical}.mkv", "Smaug",
                                       2013, info(186.6), DOS, "eng", "")
    assert ed == "Extended"
    assert ("edition", "warn") in checks(found)


def test_matching_filename_edition_is_quiet():
    found, ed = check.check_movie_file(
        "/m/The Hobbit - An Unexpected Journey (2012) {edition-Extended}.mkv", "x", 2012,
        info(182.5), AUJ, "eng", "")
    assert ed == "Extended" and "edition" not in [c for c, _ in checks(found)]


def test_missing_english_forced_subtitles_named():
    found, _ = check.check_movie_file("/m/a.mkv", "AUJ", 2012, info(182.5), AUJ, "eng", "")
    f = next(f for f in found if f["check"] == "languages")
    assert "English forced" in f["message"] and "Russian" in f["message"]


def test_sidecar_forced_subtitles_count(tmp_path: Path):
    movie = tmp_path / "AUJ (2012).mkv"
    movie.write_bytes(b"")
    (tmp_path / "AUJ (2012).eng.forced.srt").write_text("1\n", encoding="utf-8")
    found, _ = check.check_movie_file(str(movie), "AUJ", 2012, info(182.5), AUJ, "eng", "")
    assert "languages" not in [c for c, _ in checks(found)]


def test_wrong_audio_languages_named():
    audio = [{"language": "chi", "title": ""}, {"language": "fre", "title": ""}]
    found, _ = check.check_movie_file("/m/a.mkv", "AUJ", 2012, info(182.5, audio=audio),
                                      AUJ, "eng", "")
    f = next(f for f in found if "audio" in f["message"])
    assert "Chinese" in f["message"] and "French" in f["message"]


def test_commentary_does_not_count_as_audio():
    audio = [{"language": "eng", "title": "Commentary"}, {"language": "spa", "title": ""}]
    found, _ = check.check_movie_file("/m/a.mkv", "AUJ", 2012, info(182.5, audio=audio),
                                      AUJ, "eng", "")
    assert any("no English audio" in f["message"] or "found Spanish" in f["message"]
               for f in found)


def test_temp_name_and_short_runtime_are_incomplete():
    k = known.movie(120)
    found, _ = check.check_movie_file("/m/muxing.tmp.mkv", "Fellowship", 2001, info(95), k,
                                      "eng", "", tail_errors=["Invalid NAL unit size"])
    assert checks(found).count(("incomplete", "error")) == 2  # short + tail decode errors
    assert ("incomplete", "warn") in checks(found)  # temp name


def test_known_lookup_by_title_and_year():
    assert known.movie(None, "The Hobbit - The Desolation of Smaug", 2013)["tmdb"] == 57158
    assert known.movie(None, "The Hobbit - The Desolation of Smaug", 1999) is None


def test_plex_name():
    assert check.plex_name("The Lord of the Rings: The Two Towers", 2002, "Extended", ".mkv") \
        == "The Lord of the Rings - The Two Towers (2002) {edition-Extended}.mkv"


def test_mismatch_names_codec_size_shape_and_hdr():
    auj = info(182.5)
    dos = info(186.6, video={**HDR, "codec": "av1", "width": 3840})
    botfa = info(164, video={**HDR, "width": 3840, "height": 2160})
    sdr = info(201, video={**HDR, "hdr": "SDR", "width": 3840})
    found = check.mismatch("The Hobbit", [("AUJ", "/a", auj, None), ("DOS", "/d", dos, None),
                                          ("BOTFA", "/b", botfa, "3840:1600:0:280"),
                                          ("ROTK", "/r", sdr, None)])
    text = " ".join(f["message"] for f in found)
    assert "video codec" in text and "av1" in text
    assert "frame size" in text and "3840x2160" in text
    assert "HDR" in text and "SDR" in text
    # BOTFA's bars are inside the picture: cropped shape 2.40 matches the others
    assert "picture shape" not in text
    size = next(f for f in found if "frame size" in f["message"])
    assert "/b" in size["files"]  # the odd ones out are named


def test_bars_inside_picture_flagged_as_shape():
    flat = info(164, video={**HDR, "width": 3840, "height": 2160})
    found = check.mismatch("set", [("A", "/a", info(182.5), None), ("B", "/b", flat, None),
                                   ("C", "/c", info(186.6), None)])
    shape = next(f for f in found if "picture shape" in f["message"])
    assert shape["file"] == "/b" and "crop" in shape["fix"]


class FakeLib:
    def __init__(self, eps):
        self.eps = eps

    def list_movies(self):
        return [], {}

    def list_series(self):
        return [Series(key="plex:1", source="plex", title="Attack on Titan", tvdb_id=267440)], {}

    def episodes(self, key):
        return self.eps


def test_aot_missing_final_chapters_and_ovas(cfg):
    have = {1: 25, 2: 12, 3: 22, 4: 28}
    eps = [Episode(season=s, number=n, file=None) for s, c in have.items()
           for n in range(1, c + 1)]
    chk = check.LibraryChecker(cfg, Database(":memory:"), lambda p: {}, FakeLib(eps))
    eps_with_files = [Episode(season=e.season, number=e.number, file=f"/aot/{e.id}.mkv")
                      for e in eps]
    chk.library = FakeLib(eps_with_files)
    chk.prober = lambda p: info(24, audio=[{"language": "chi", "title": ""},
                                           {"language": "fre", "title": ""}])
    found = chk.check_series(chk.library.list_series()[0][0])
    text = [f["message"] for f in found]
    assert any("Final Chapters missing (S04E29, S04E30)" in t for t in text)
    assert any("OVAs missing" in t for t in text)
    assert any(t.startswith("87 of 89 episodes") for t in text)
    assert any("no Japanese or English audio; found Chinese, French" in t for t in text)
    final = next(f for f in found if "Final Chapters" in f["message"])
    assert final["items"] == ["S04E29", "S04E30"]  # Health's "Show list"

    # a whole run tags every series finding with the show it belongs to
    run = chk.run(timelines=[])
    assert run["findings"] and all(f["ref"] == {"kind": "series", "key": "plex:1"}
                                   for f in run["findings"])


def test_check_endpoint_and_confirmed_rename(client, tmp_path: Path):
    root = tmp_path / "movies"
    d = root / "The Hobbit - The Desolation of Smaug (2013)"
    d.mkdir(parents=True)
    f = d / "Hobbit.Smaug.2013.2160p.mkv"
    f.write_bytes(b"x")
    core: Core = client.app_ref.state.core
    core.cfg.library.movies.path = str(root)
    core.library.refresh()
    core.prober = lambda _p: info(186.6)
    core.library.prober = core.prober

    r = client.get("/api/library/check").json()
    assert r["summary"]["files"] == 1
    assert any(x["check"] == "edition" for x in r["findings"])
    ren = r["renames"][0]
    assert Path(ren["to"]).name == \
        "The Hobbit - The Desolation of Smaug (2013) {edition-Extended}.mkv"

    # only the check's own proposals are accepted
    bad = client.post("/api/library/rename", json={"file": str(f), "to": str(d / "x.mkv")})
    assert bad.status_code == 400 and f.exists()
    assert ren["writable"] is True and "rename_blocked" not in r
    ok = client.post("/api/library/rename", json={"file": ren["file"], "to": ren["to"]})
    assert ok.status_code == 200 and Path(ren["to"]).exists() and not f.exists()


def test_rename_refused_on_a_read_only_library(client, tmp_path: Path, monkeypatch):
    root = tmp_path / "movies"
    d = root / "The Hobbit - The Desolation of Smaug (2013)"
    d.mkdir(parents=True)
    f = d / "Hobbit.Smaug.2013.2160p.mkv"
    f.write_bytes(b"x")
    core: Core = client.app_ref.state.core
    core.cfg.library.movies.path = str(root)
    core.library.refresh()
    core.prober = lambda _p: info(186.6)
    core.library.prober = core.prober
    # what a Docker :ro mount looks like to the app
    monkeypatch.setattr(check, "folder_writable", lambda _file: False)

    r = client.get("/api/library/check").json()
    ren = r["renames"][0]
    assert ren["writable"] is False and "read-only" in r["rename_blocked"]
    res = client.post("/api/library/rename", json={"file": ren["file"], "to": ren["to"]})
    assert res.status_code == 400 and "read-only" in res.text and f.exists()


def test_botfa_bars_inside_the_frame_named():
    botfa = info(164, video={**HDR, "width": 3840, "height": 2160})
    found = check.mismatch("The Hobbit", [
        ("AUJ", "/a", info(182.5), "3824:1600:6:4"),
        ("DOS", "/d", info(186.6, video={**HDR, "width": 3840}), "3840:1600:0:4"),
        ("BOTFA", "/b", botfa, "3840:1600:0:280")])
    bars = [f for f in found if "black bars" in f["message"]]
    assert len(bars) == 1 and bars[0]["file"] == "/b"
    assert "3840x1600 picture" in bars[0]["message"] and "280 px" in bars[0]["message"]
    assert not any("picture shape" in f["message"] for f in found)  # same 2.4:1 once cropped

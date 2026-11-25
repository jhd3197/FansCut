"""Read models for the redesigned UI: movies, collections, details, my cuts."""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import quote

import responses

from fanscut.browse import _franchise, _show_members, video_facts
from fanscut.models import Series
from fanscut.render.ffmpeg import PROBE_VERSION, Prober
from tests.test_suggestions import (  # noqa: F401  (took_library is a fixture)
    HOBBIT,
    PLEX,
    TOOK_RECIPE,
    plex_movie,
    took_library,
)


def test_movies_and_collections(client, took_library):  # noqa: F811
    movies = client.get("/api/library/movies").json()["movies"]
    assert len(movies) == 3 and movies[0]["minutes"] == 100
    # Plex tag plus the known set from the built-in table
    assert {"The Hobbit Collection", "The Hobbit"} <= set(movies[0]["collections"])
    colls = {c["name"]: c for c in client.get("/api/library/collections").json()["collections"]}
    assert colls["The Hobbit Collection"]["films"] == 3
    one = client.get(f"/api/library/collections/{quote('The Hobbit Collection')}").json()
    assert [f["year"] for f in one["films"]] == [2012, 2013, 2014]
    assert any(s["kind"] == "collection_merge" for s in one["suggestions"])
    assert client.get("/api/library/collections/Nope").status_code == 404


def test_movie_detail_lists_cuts_using_it(client, took_library):  # noqa: F811
    tid = client.post("/api/series/movie:49051/timelines", json=TOOK_RECIPE).json()["id"]
    key = client.get("/api/library/movies").json()["movies"][0]["key"]
    m = client.get(f"/api/library/movies/{quote(key, safe='')}").json()
    assert m["tmdb_id"] == 49051 and m["probe"] is None  # file not on disk in tests
    assert [c["id"] for c in m["cuts"]] == [tid]
    assert m["collection_films"][0]["films"]
    cuts = client.get("/api/cuts").json()
    assert cuts["cuts"][0]["tmdb_ids"] == [49051, 57158, 122917]


def test_last_check_cached(client):
    assert client.get("/api/library/check/last").json()["summary"] is None
    client.get("/api/library/check")
    last = client.get("/api/library/check/last").json()
    assert last["summary"] is not None and last["checked_at"]


def test_episode_info(client):
    r = client.get("/api/series/sonarr:7/episodes/S01E02/info").json()
    assert r["id"] == "S01E02" and r["prev"] == "S01E01"
    assert client.get("/api/series/sonarr:7/episodes/S09E09/info").status_code == 404


def test_video_facts_labels():
    assert video_facts(None) is None
    uhd = {"video": {"width": 3840, "height": 1600, "codec": "hevc", "hdr": "HDR10"}}
    assert video_facts(uhd) == {"res": "4K", "hdr": "HDR10", "codec": "HEVC"}
    hd = {"video": {"width": 1920, "height": 800, "codec": "h264", "hdr": "SDR"}}
    assert video_facts(hd) == {"res": "1080p", "hdr": None, "codec": "H.264"}


def test_movie_rows_use_cached_probes_only(client, took_library):  # noqa: F811
    """Listing reads the probe cache, never the files: unprobed copies read as None."""
    core = client.app_ref.state.core
    core.prober = Prober(core.cfg.ffmpeg, core.db)
    info = {"v": PROBE_VERSION, "video": {"width": 3840, "height": 2160, "codec": "hevc",
                                          "hdr": "HDR10"}}
    core.db.execute("INSERT INTO probe_cache (path, mtime, size, info) VALUES (?,?,?,?)",
                    ("/movies/hobbit0.mkv", 0, 0, json.dumps(info)))
    rows = {m["file"]: m for m in client.get("/api/library/movies").json()["movies"]}
    assert rows["/movies/hobbit0.mkv"]["video"] == {"res": "4K", "hdr": "HDR10", "codec": "HEVC"}
    assert rows["/movies/hobbit1.mkv"]["video"] is None  # "Not analysed"


def test_versions_grouped_and_source_for(client, took_library):  # noqa: F811
    films = [plex_movie(i, t, y, tmdb, "The Hobbit Collection")
             for i, (t, y, tmdb) in enumerate(HOBBIT)]
    theatrical = plex_movie(9, "An Unexpected Journey", 2012, 49051)
    theatrical["Media"][0]["Part"][0]["file"] = "/movies/hobbit-theatrical.mkv"
    took_library.replace(responses.GET, f"{PLEX}/library/sections/2/all",
                         json={"MediaContainer": {"Metadata": [*films, theatrical]}})
    core = client.app_ref.state.core
    core.library.refresh()
    client.post("/api/series/movie:49051/timelines", json=TOOK_RECIPE)

    movies = client.get("/api/library/movies").json()["movies"]
    aujs = [m for m in movies if m["tmdb_id"] == 49051]
    assert len(aujs) == 2
    head = next(m for m in aujs if "versions" in m)
    tail = next(m for m in aujs if "version_of" in m)
    assert tail["version_of"] == head["key"] and len(head["versions"]) == 2
    assert head["source_for"] == [TOOK_RECIPE["title"]]  # the copy the cut uses leads

    one = client.get(f"/api/library/movies/{quote(head['key'], safe='')}").json()
    assert [v["key"] for v in one["other_versions"]] == [tail["key"]]
    assert one["source_for"] == [TOOK_RECIPE["title"]]


def test_shows_join_their_franchise_collection():
    assert _franchise("Attack on Titan Collection") == "attack on titan"
    aot = Series(key="plex:1", source="plex", title="Attack on Titan")
    tagged = Series(key="plex:2", source="plex", title="Other", collections=["Middle-earth"])
    lib = SimpleNamespace(list_series=lambda: ([aot, tagged], {}))
    core = SimpleNamespace(library=lib)
    got = _show_members(core, ["Attack on Titan Collection", "Middle-earth", "The Hobbit"])
    assert got == {"Attack on Titan Collection": [aot], "Middle-earth": [tagged]}


def test_series_list_carries_counts_and_audio_note(client):
    core = client.app_ref.state.core
    core.cfg.data_dir.mkdir(parents=True, exist_ok=True)
    (core.cfg.data_dir / "library-check.json").write_text(json.dumps({"findings": [
        {"check": "languages", "severity": "warn", "message": "no English audio",
         "ref": {"kind": "series", "key": "sonarr:7"}}]}))
    s = client.get("/api/library/series").json()["series"][0]
    assert s["episode_count"] == 6 and "season_count" in s and "episode_total" in s
    assert s["audio_note"] == "no English audio"


def test_series_season_and_episode_counts_per_source():
    from fanscut.library.service import Library

    sonarr = Library._sonarr_series({
        "id": 1, "title": "AoT", "statistics": {"episodeFileCount": 87, "episodeCount": 89},
        "seasons": [{"seasonNumber": n} for n in range(5)]})
    assert (sonarr.season_count, sonarr.episode_count, sonarr.episode_total) == (4, 87, 89)
    plex = Library._plex_series({"ratingKey": "3", "title": "AoT", "childCount": 4,
                                 "leafCount": 87, "Collection": [{"tag": "Titans"}]})
    assert (plex.season_count, plex.episode_total, plex.collections) == (4, 87, ["Titans"])


def test_collection_groups_one_copy_per_film_and_tmdb_ids():
    """Juan's LOTR: Fellowship twice (a real file and Plex's muxing.tmp.mkv)."""
    from fanscut.browse import collection_groups, find_group
    from fanscut.models import Movie

    def film(key, title, year, tmdb, file, **kw):
        return Movie(key=key, source=key.split(":")[0].replace("plexmovie", "plex"),
                     title=title, year=year, tmdb_id=tmdb, file=file,
                     collections=["The Lord of the Rings"], **kw)

    fotr = film("radarr:530", "Fellowship", 2001, 120, "/m/FOTR.Extended.2160p.mkv",
                tmdb_collection=119)
    tmp = film("plexmovie:30868", "Fellowship", 2001, 120, "/m/muxing.tmp.mkv", plex_key="30868")
    ttt = film("radarr:529", "Two Towers", 2002, 121, "/m/TTT.mkv", tmdb_collection=119,
               plex_key="31000")
    (g,) = collection_groups([tmp, fotr, ttt])
    assert g["id"] == "tmdb-119" and g["name"] == "The Lord of the Rings"
    assert [m.key for m in g["movies"]] == ["radarr:530", "radarr:529"]  # the temp copy lost
    assert g["movies"][0].plex_key == "30868"  # borrowed from the other copy: playlists work
    assert find_group([tmp, fotr, ttt], "The Lord of the Rings")["id"] == "tmdb-119"


def test_radarr_movie_keeps_tmdb_collection():
    from fanscut.library.service import Library

    lib = Library.__new__(Library)
    lib._map = lambda p: p
    m = lib._radarr_movie({"id": 530, "title": "FOTR", "tmdbId": 120,
                           "collection": {"title": "The Lord of the Rings Collection",
                                          "tmdbId": 119}})
    assert m.tmdb_collection == 119


def test_mixed_lists_are_not_franchise_groups():
    """"IMDb Top 250" spans franchises: no merge or watch order for it."""
    from fanscut.browse import collection_groups
    from fanscut.models import Movie

    def film(i, cid, coll):
        return Movie(key=f"radarr:{i}", source="radarr", title=f"F{i}", year=2000 + i,
                     tmdb_id=i, file=f"/m/{i}.mkv", tmdb_collection=cid, collections=[coll])

    mixed = [film(1, 119, "IMDb Top 250"), film(2, 230, "IMDb Top 250")]
    auto = [film(3, None, "Popular on My Server"), film(4, None, "Popular on My Server")]
    one = [film(5, 10, "Alien"), film(6, 10, "Alien")]
    assert [g["name"] for g in collection_groups(mixed + auto + one)] == ["Alien"]


def test_folder_scan_sorts_temp_files_last():
    from fanscut.library.scan import _TEMP

    assert _TEMP.search("Film.tmp.mkv") and _TEMP.search("Film.partial.mkv")
    assert _TEMP.search("muxing.mkv") and not _TEMP.search("Film.tmpl.mkv")


def test_harry_potter_groups_all_eight_films_even_when_plex_tags_three():
    from fanscut.browse import collection_groups
    from fanscut.models import Movie

    films = [(671, "Harry Potter and the Sorcerer's Stone", 2001, []),
             (672, "Harry Potter and the Chamber of Secrets", 2002, ["Popular on My Server"]),
             (673, "Harry Potter and the Prisoner of Azkaban", 2004, ["Harry Potter"]),
             (674, "Harry Potter and the Goblet of Fire", 2005, []),
             (675, "Harry Potter and the Order of the Phoenix", 2007, ["Harry Potter"]),
             (767, "Harry Potter and the Half-Blood Prince", 2009, ["Harry Potter"]),
             (12444, "Harry Potter and the Deathly Hallows: Part 1", 2010, []),
             (12445, "Harry Potter and the Deathly Hallows: Part 2", 2011, [])]
    movies = [Movie(key=f"plexmovie:{t}", source="plex", title=n, year=y, tmdb_id=t, file=f"/m/{t}.mkv",
                    collections=c) for t, n, y, c in films]
    hp = next(g for g in collection_groups(movies) if g["name"] == "Harry Potter")
    assert [m.tmdb_id for m in hp["movies"]] == [t for t, *_ in films]

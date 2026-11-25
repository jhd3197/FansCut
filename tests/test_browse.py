"""Read models for the redesigned UI: movies, collections, details, my cuts."""

from __future__ import annotations

from urllib.parse import quote

from tests.test_suggestions import TOOK_RECIPE, took_library  # noqa: F401  (fixture)


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

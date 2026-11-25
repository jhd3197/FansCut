"""First-run suggestions: community recipes, Plex collections, canon cuts, install."""

import json

import pytest
import responses

from tests.conftest import PLEX, SERIES
from tests.test_api import E, matched  # noqa: F401  (fixture)

HOBBIT = [
    ("An Unexpected Journey", 2012, 49051),
    ("The Desolation of Smaug", 2013, 57158),
    ("The Battle of the Five Armies", 2014, 122917),
]

TOOK_RECIPE = {
    "schema": 2, "kind": "timeline", "title": "The Hobbit — There and Back Again (Took cut)",
    "series": {"title": "The Hobbit"},
    "sources": {f"M{tmdb}": {"kind": "movie", "tmdb": tmdb, "edition": "extended",
                             "duration": 7000.0} for _t, _y, tmdb in HOBBIT},
    "parts": [{"title": "There and Back Again", "segments": [
        {"src": f"M{tmdb}", "start": 300.0, "end": 6800.0} for _t, _y, tmdb in HOBBIT]}],
    "credits": {"author": "test"},
}


def plex_movie(i, title, year, tmdb, collection=None):
    m = {"ratingKey": str(100 + i), "title": f"{title} (Extended Edition)" if collection else title,
         "year": year, "Guid": [{"id": f"tmdb://{tmdb}"}],
         "Media": [{"duration": 6_000_000, "Part": [{"file": f"/movies/hobbit{i}.mkv"}]}]}
    if collection:
        m["Collection"] = [{"tag": collection}]
    return m


@pytest.fixture
def took_library(app_cfg, tmp_path):
    """Plex with the three extended Hobbit films in a 'The Hobbit Collection'."""
    app_cfg.library.plex.url = PLEX
    app_cfg.library.plex.token = "token"
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.GET, f"{PLEX}/", json={"MediaContainer": {"machineIdentifier": "abc"}})
        rsps.add(responses.GET, f"{PLEX}/library/sections",
                 json={"MediaContainer": {"Directory": [
                     {"key": "2", "type": "movie", "title": "Movies"}]}})
        rsps.add(responses.GET, f"{PLEX}/library/sections/2/all",
                 json={"MediaContainer": {"Metadata": [
                     plex_movie(i, t, y, tmdb, "The Hobbit Collection")
                     for i, (t, y, tmdb) in enumerate(HOBBIT)]}})
        rdir = tmp_path / "data" / "recipes"
        rdir.mkdir(parents=True, exist_ok=True)
        (rdir / "took.json").write_text(json.dumps(TOOK_RECIPE))
        yield rsps


def test_took_case_recipe_is_ready(client, took_library):
    """The reference case: the Took recipe on a library with the three extended films."""
    sugg = client.get("/api/suggestions").json()["suggestions"]
    took = next(s for s in sugg if s["id"] == "recipe:took.json")
    assert took["status"] == "ready", took["missing"]
    assert took["kind"] == "recipe" and took["detail"]["segments"] == 3


def test_collection_templates_appear(client, took_library):
    sugg = client.get("/api/suggestions").json()["suggestions"]
    merge = next(s for s in sugg if s["kind"] == "collection_merge")
    order = next(s for s in sugg if s["kind"] == "watch_order")
    assert merge["status"] == "ready" and "merged" in merge["title"]
    assert "2012" in str(merge["detail"]["years"])
    assert order["status"] == "ready" and order["missing"] == []


def test_suggestions_name_the_library_items_they_use(client, took_library):
    """Movie and Show pages filter /suggestions by these keys."""
    movie_keys = {m["key"] for m in client.get("/api/library/movies").json()["movies"]}
    sugg = client.get("/api/suggestions").json()["suggestions"]
    took = next(s for s in sugg if s["id"] == "recipe:took.json")
    merge = next(s for s in sugg if s["kind"] == "collection_merge")
    assert len(took["keys"]) == 3 and set(took["keys"]) <= movie_keys
    assert set(merge["keys"]) == set(took["keys"])


def test_missing_edition_is_named(client, took_library, tmp_path):
    doc = json.loads(json.dumps(TOOK_RECIPE))  # deep copy: the fixture is shared
    doc["sources"] = {"M49051": {"kind": "movie", "tmdb": 49051, "edition": "extended",
                                 "duration": 7000.0},
                      "M99999": {"kind": "movie", "tmdb": 99999, "edition": "extended",
                                 "duration": 7000.0}}
    doc["parts"][0]["segments"] = [{"src": "M49051", "start": 1, "end": 100},
                                   {"src": "M99999", "start": 1, "end": 100}]
    (tmp_path / "data" / "recipes" / "partial.json").write_text(json.dumps(doc))
    sugg = client.get("/api/suggestions").json()["suggestions"]
    s = next(x for x in sugg if x["id"] == "recipe:partial.json")
    assert s["status"] == "partly"
    assert s["missing"] == [{"src": "M99999",
                             "reason": "movie 99999 (extended) not in the library"}]


def test_missing_audio_language_is_named(client, took_library, app_cfg, monkeypatch):
    app_cfg.render.language = "jpn"
    core = client.app_ref.state.core
    core.prober = lambda _f: {"duration": 6000.0, "audio_tracks": [
        {"codec": "aac", "sample_rate": 48000, "channels": 2, "language": "eng"}]}
    sugg = client.get("/api/suggestions").json()["suggestions"]
    took = next(s for s in sugg if s["id"] == "recipe:took.json")
    assert took["status"] == "partly"
    assert took["missing"][0]["reason"] == "no 'jpn' audio track"


def test_canon_cut_suggestion(matched):  # noqa: F811  (matched = series matched to MAL)
    sugg = matched.get("/api/suggestions").json()["suggestions"]
    canon = next((s for s in sugg if s["kind"] == "canon_cut"), None)
    assert canon is not None and canon["detail"]["series_key"] == SERIES
    assert canon["detail"]["filler_episodes"] >= 1
    assert canon["keys"] == [SERIES]


def test_install_canon_queues_render(matched):  # noqa: F811
    r = matched.post(f"/api/suggestions/canon:{SERIES}/install").json()
    assert r["action"] == "render"
    job = matched.get(f"/api/jobs/{r['jobs'][0]}").json()
    assert job["kind"] == "render" and job["payload"]["options"] == {"preset": "canon_only"}


def test_install_recipe_queues_sync_then_render(client, took_library):
    r = client.post("/api/suggestions/recipe:took.json/install").json()
    assert r["action"] == "render"
    jobs = [client.get(f"/api/jobs/{j}").json() for j in r["jobs"]]
    assert [j["kind"] for j in jobs] == ["timeline_sync", "timeline_render"]
    tl = client.get(f"/api/timelines/{r['timeline']}").json()
    assert tl["doc"]["title"].startswith("The Hobbit")


def test_install_merge_builds_trilogy_doc(client, took_library):
    r = client.post("/api/suggestions/merge:The Hobbit Collection/install").json()
    doc = client.get(f"/api/timelines/{r['timeline']}").json()["doc"]
    assert len(doc["parts"]) == 1 and len(doc["parts"][0]["segments"]) == 3
    assert all(s["kind"] == "movie" for s in doc["sources"].values())
    jobs = [client.get(f"/api/jobs/{j}").json() for j in r["jobs"]]
    assert [j["kind"] for j in jobs] == ["timeline_sync", "timeline_render"]


def test_install_watch_order_makes_playlist_no_render(client, took_library):
    rsps = took_library
    rsps.add(responses.POST, f"{PLEX}/playlists",
             json={"MediaContainer": {"Metadata": [{"ratingKey": "777"}]}})
    rsps.add(responses.PUT, f"{PLEX}/playlists/777/items", json={})
    r = client.post("/api/suggestions/order:The Hobbit Collection/install").json()
    assert r == {"action": "playlist", "playlist_id": "777"}
    posts = [c for c in rsps.calls if c.request.method in ("POST", "PUT")]
    assert len(posts) == 3  # create with item 1, add items 2 and 3
    from urllib.parse import unquote
    assert "library/metadata/100" in unquote(posts[0].request.url)
    assert client.get("/api/jobs").json()["jobs"] == []  # nothing rendered

    # My cuts lists it (the Playlists filter), with a link into Plex
    cuts = client.get("/api/cuts").json()["cuts"]
    pl = next(c for c in cuts if c["kind"] == "playlist")
    assert pl["playlist_id"] == "777" and len(pl["films"]) == 3
    url = client.get("/api/playlists/777/plex").json()["url"]
    assert url == f"{PLEX}/web/index.html#!/server/abc/playlist?key=%2Fplaylists%2F777"
    assert client.get("/api/playlists/999/plex").status_code == 404


def test_movie_play_links_into_plex(client, took_library):
    key = client.get("/api/library/movies").json()["movies"][0]["key"]
    url = client.get(f"/api/movies/{key}/plex").json()["url"]
    assert url.startswith(f"{PLEX}/web/index.html#!/server/abc/details?key=")
    assert url.endswith("%2Flibrary%2Fmetadata%2F100")


def test_cut_opens_in_plex_once_scanned(client, took_library):
    tid = client.post("/api/series/movie:49051/timelines", json=TOOK_RECIPE).json()["id"]
    # Plex's title filter also returns near misses (the Hobbit films): not the cut
    assert client.get(f"/api/cuts/{tid}/plex").status_code == 404  # not scanned yet
    core = client.app_ref.state.core
    from fanscut.timeline import render as tl_render

    doc = TOOK_RECIPE
    show = tl_render.show_name(core._timeline_series_title("movie:49051", doc), doc["title"])
    took_library.replace(responses.GET, f"{PLEX}/library/sections/2/all", json={
        "MediaContainer": {"Metadata": [{"ratingKey": "4242", "title": show}]}})
    url = client.get(f"/api/cuts/{tid}/plex").json()["url"]
    assert url.endswith("%2Flibrary%2Fmetadata%2F4242")
    assert core.db.one("SELECT rating_key FROM plex_items WHERE ref = ?", (tid,))[0] == "4242"

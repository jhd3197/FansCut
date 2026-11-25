"""Posters and backdrops: Plex first, then Radarr/Sonarr, then files beside the media."""

from __future__ import annotations

from pathlib import Path

import responses

from fanscut.art import ArtStore, art_ref, local_image, media_type
from fanscut.library.service import Library, _remote_image
from fanscut.models import Movie
from tests.conftest import SERIES

JPEG = b"\xff\xd8\xff\xe0" + b"poster" * 10
PNG = b"\x89PNG\r\n\x1a\n" + b"art" * 10
SONARR = "http://sonarr.test:8989"
PLEX = "http://plex.test:32400"


def test_media_type_sniffs_the_bytes():
    assert media_type(JPEG) == "image/jpeg"
    assert media_type(PNG) == "image/png"
    assert media_type(b"nope") == "application/octet-stream"


def test_local_image_follows_the_plex_and_jellyfin_names(tmp_path):
    (tmp_path / "Folder.JPG").write_bytes(JPEG)
    (tmp_path / "fanart.png").write_bytes(PNG)
    (tmp_path / "notes.txt").write_text("x")
    assert local_image(tmp_path, "poster").name == "Folder.JPG"
    assert local_image(tmp_path, "backdrop").name == "fanart.png"
    assert local_image(tmp_path / "missing", "poster") is None


def test_sonarr_cover_through_the_api_and_cached(client, sonarr_mock):
    sonarr_mock.add(responses.GET, f"{SONARR}/api/v3/MediaCover/7/poster-500.jpg",
                    body=JPEG, content_type="image/jpeg")
    r = client.get(f"/api/art/poster/{SERIES}?w=400")
    assert r.status_code == 200 and r.content == JPEG
    assert r.headers["content-type"] == "image/jpeg"
    assert "max-age" in r.headers["cache-control"]
    calls = len([c for c in sonarr_mock.calls if "MediaCover" in c.request.url])
    assert client.get(f"/api/art/poster/{SERIES}?w=400").content == JPEG
    assert len([c for c in sonarr_mock.calls if "MediaCover" in c.request.url]) == calls


def test_missing_art_is_404_and_the_miss_is_remembered(client, sonarr_mock):
    sonarr_mock.add(responses.GET, f"{SONARR}/api/v3/MediaCover/7/fanart.jpg", status=404)
    assert client.get(f"/api/art/backdrop/{SERIES}").status_code == 404
    calls = len([c for c in sonarr_mock.calls if "MediaCover" in c.request.url])
    assert client.get(f"/api/art/backdrop/{SERIES}").status_code == 404
    assert len([c for c in sonarr_mock.calls if "MediaCover" in c.request.url]) == calls
    assert client.get(f"/api/art/cover/{SERIES}").status_code == 400  # not a kind


@responses.activate
def test_plex_beats_the_folder_copy_of_the_same_film(cfg, tmp_path):
    movie_dir = tmp_path / "movies" / "The Long Voyage (2001)"
    movie_dir.mkdir(parents=True)
    (movie_dir / "The Long Voyage (2001).mkv").write_bytes(b"x")
    (movie_dir / "poster.jpg").write_bytes(PNG)
    cfg.library.movies.path = str(tmp_path / "movies")
    cfg.library.plex.url, cfg.library.plex.token = PLEX, "t"
    responses.get(f"{PLEX}/library/sections", json={"MediaContainer": {"Directory": [
        {"key": "1", "type": "movie", "title": "Movies"}]}})
    responses.get(f"{PLEX}/library/sections/1/all", json={"MediaContainer": {"Metadata": [
        {"ratingKey": "55", "title": "The Long Voyage", "year": 2001,
         "Media": [{"Part": [{"file": str(movie_dir / "The Long Voyage (2001).mkv")}]}]}]}})
    responses.get(f"{PLEX}/photo/:/transcode", body=JPEG, content_type="image/jpeg")
    lib = Library(cfg)
    store = ArtStore(lib, tmp_path / "art")
    folder_key = next(m.key for m in lib.list_movies(merged=False)[0] if m.source != "plex")

    assert store.get(folder_key, "poster", 300) == JPEG  # Plex's copy wins
    assert "url=%2Flibrary%2Fmetadata%2F55%2Fthumb" in responses.calls[-1].request.url

    cfg.library.plex.url = ""  # Plex gone: the file beside the movie is used
    lib2 = Library(cfg)
    assert ArtStore(lib2, tmp_path / "art2").get(folder_key, "poster", 300) == PNG


def test_unknown_key_has_no_art(cfg, tmp_path):
    assert ArtStore(Library(cfg), tmp_path / "art").get("radarr:404") is None
    assert not list(Path(tmp_path / "art").glob("*[!e]"))  # only the .none marker


def test_radarr_remote_urls_go_to_the_browser_directly():
    images = [{"coverType": "poster", "url": "/MediaCover/1/poster.jpg",
               "remoteUrl": "https://image.tmdb.org/t/p/original/p.jpg"},
              {"coverType": "fanart", "remoteUrl": "https://image.tmdb.org/t/p/original/f.jpg"}]
    assert _remote_image(images, "poster") == "https://image.tmdb.org/t/p/original/p.jpg"
    assert _remote_image([{"coverType": "poster", "url": "/MediaCover/1/poster.jpg"}],
                         "poster") is None  # the *arr-local path is not reachable
    m = Movie("radarr:1", "radarr", "The Long Voyage", poster=_remote_image(images, "poster"),
              backdrop=_remote_image(images, "fanart"))
    # the fragment names the item, so the UI can fall back to /api/art if the CDN fails
    assert art_ref(m) == "https://image.tmdb.org/t/p/original/p.jpg#k=radarr%3A1"
    assert art_ref(m, "backdrop").startswith("https://image.tmdb.org/t/p/original/f.jpg#k=")
    # a Plex path (or nothing) stays a key, served through /api/art
    assert art_ref(Movie("plexmovie:9", "plex", "X", poster="/library/metadata/9/thumb/1")) == \
        "plexmovie:9"

@responses.activate
def test_plex_skips_overlaid_uploads_for_the_clean_agent_poster():
    from fanscut.clients.plex import PlexClient

    responses.get(f"{PLEX}/library/metadata/9/posters", json={"MediaContainer": {"Metadata": [
        {"key": "upload://posters/kometa-overlay", "provider": "upload", "selected": True},
        {"key": "https://image.tmdb.org/t/p/original/clean.jpg", "provider": "tmdb"}]}})
    assert PlexClient(PLEX, "t").clean_art_path("9") == "https://image.tmdb.org/t/p/original/clean.jpg"


# -- cache behaviour ------------------------------------------------------------
def _store(tmp_path, fetch):
    from fanscut import art as art_mod

    store = ArtStore(None, tmp_path / "art")  # type: ignore[arg-type]
    store.fetch = fetch  # type: ignore[method-assign]
    return store, art_mod


def test_transient_error_is_not_remembered_as_no_art(tmp_path):
    from fanscut.errors import ClientError

    calls = []

    def fetch(key, kind, width):
        calls.append(key)
        if len(calls) == 1:
            raise ClientError("Plex unreachable")
        return JPEG

    store, _ = _store(tmp_path, fetch)
    assert store.get("plex:1") is None  # down: nothing to show...
    assert store.get("plex:1") == JPEG  # ...but the next view asks again
    assert not list((tmp_path / "art").glob("*.none"))


def test_stale_art_is_served_and_kept_when_the_refresh_fails(tmp_path):
    import os
    import time

    from fanscut.errors import ClientError

    state = {"fail": False}

    def fetch(key, kind, width):
        if state["fail"]:
            raise ClientError("Plex unreachable")
        return JPEG

    store, art_mod = _store(tmp_path, fetch)
    assert store.lookup("plex:1").status == "MISS"
    path = store._path("plex:1", "poster", 500)
    old = time.time() - art_mod.HIT_TTL - 10
    os.utime(path, (old, old))
    state["fail"] = True
    art = store.lookup("plex:1")
    assert art.status == "STALE" and art.data == JPEG  # served at once, not dropped
    store._pool.shutdown(wait=True)
    assert path.exists()  # the failed refresh kept the old file
    assert store.lookup("plex:1").data == JPEG


def test_one_fetch_per_image_under_concurrency(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    calls = []
    lock = threading.Lock()

    def fetch(key, kind, width):
        with lock:
            calls.append(key)
        threading.Event().wait(0.05)  # time.sleep is stubbed in tests
        return JPEG

    store, _ = _store(tmp_path, fetch)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _i: store.get("plex:1"), range(8)))
    assert results == [JPEG] * 8 and len(calls) == 1


def test_eviction_trims_the_oldest(tmp_path):
    import os
    import time

    store, _ = _store(tmp_path, lambda k, kind, w: b"x" * 1000)
    store.max_bytes = 2500
    for i in range(4):
        store.get(f"plex:{i}")
        p = store._path(f"plex:{i}", "poster", 500)
        os.utime(p, (time.time() - 100 + i, time.time() - 100 + i))
    store.evict()
    left = {i for i in range(4) if store._path(f"plex:{i}", "poster", 500).exists()}
    assert left == {2, 3}
    assert store.stats()["images"] == 2


def test_art_route_etag_and_cache_header(client, sonarr_mock):
    sonarr_mock.add(responses.GET, f"{SONARR}/api/v3/MediaCover/7/poster-500.jpg",
                    body=JPEG, content_type="image/jpeg")
    r = client.get(f"/api/art/poster/{SERIES}")
    assert r.headers["x-art-cache"] == "MISS" and r.headers["etag"]
    again = client.get(f"/api/art/poster/{SERIES}", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304 and again.headers["x-art-cache"] == "HIT"


def test_remote_keys_only_fetch_the_image_cdns(tmp_path):
    store = ArtStore(None, tmp_path / "art")  # type: ignore[arg-type]
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, "https://image.tmdb.org/t/p/w342/p.jpg", body=JPEG,
                 content_type="image/jpeg")
        assert store.get("remote:https://image.tmdb.org/t/p/original/p.jpg", "poster",
                         300) == JPEG
        assert store.get("remote:http://169.254.169.254/latest", "poster", 300) is None
        assert len(rsps.calls) == 1

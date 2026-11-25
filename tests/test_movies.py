"""Movie sources: scanning, Radarr client, edition-aware resolution, validation."""

import pytest
import responses

from fanscut.library.paths import map_path  # noqa: F401  (keeps import surface honest)
from fanscut.library.scan import list_movies_folder
from fanscut.library.service import Library
from fanscut.timeline import recipe as tl
from tests.test_timeline import TWO_PART

RADARR = "http://radarr.test:7878"


def test_folder_movies_parse_tmdb_and_year(tmp_path):
    root = tmp_path / "movies"
    (root / "Tide Pool Film (2021) Theatrical {tmdb-555}").mkdir(parents=True)
    (root / "Tide Pool Film (2021) Theatrical {tmdb-555}" / "film.mkv").write_bytes(b"x")
    (root / "Tide Pool Film (2021) Extended {tmdb-555}").mkdir()
    (root / "Tide Pool Film (2021) Extended {tmdb-555}" / "film-ext.mkv").write_bytes(b"x")
    (root / "Empty Folder").mkdir()
    movies = list_movies_folder(root)
    assert len(movies) == 2
    assert all(m.tmdb_id == 555 and m.file for m in movies)
    assert {m.title for m in movies} == {"Tide Pool Film (2021) Theatrical",
                                         "Tide Pool Film (2021) Extended"}


def test_find_movie_prefers_the_asked_edition(cfg, tmp_path):
    root = tmp_path / "movies"
    for ed in ("Theatrical", "Extended"):
        d = root / f"Tide Pool Film (2021) {ed} {{tmdb-555}}"
        d.mkdir(parents=True)
        (d / "film.mkv").write_bytes(b"x")
    cfg.library.movies.path = str(root)
    lib = Library(cfg)
    th = lib.find_movie(555, edition="theatrical")
    assert th is not None and "Theatrical" in th.key
    ex = lib.find_movie(555, edition="extended")
    assert ex is not None and "Extended" in ex.key
    assert lib.find_movie(999) is None


def test_radarr_client_lists_movies():
    from fanscut.clients.radarr import RadarrClient

    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{RADARR}/api/v3/movie",
                 json=[{"id": 12, "title": "Tide Pool Film", "year": 2021, "tmdbId": 555,
                        "movieFile": {"path": "/movies/tide-pool.mkv",
                                      "mediaInfo": {"runTime": "01:45:00"}}}])
        rsps.add(responses.GET, f"{RADARR}/api/v3/system/status", json={"version": "5.0.0"})
        c = RadarrClient(RADARR, "key")
        assert c.system_status()["version"] == "5.0.0"
        assert c.list_movies()[0]["tmdbId"] == 555


def test_radarr_movies_in_library(cfg):
    cfg.library.radarr.url = RADARR
    cfg.library.radarr.api_key = "key"
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, f"{RADARR}/api/v3/movie",
                 json=[{"id": 12, "title": "Tide Pool Film", "year": 2021, "tmdbId": 555,
                        "movieFile": {"path": "/movies/tide-pool.mkv",
                                      "mediaInfo": {"runTime": "01:45:00"}}}])
        movies, errors = Library(cfg).list_movies()
    assert errors == {}
    m = movies[0]
    assert (m.key, m.tmdb_id, m.duration) == ("radarr:12", 555, 6300.0)
    assert m.file.endswith("tide-pool.mkv")


MOVIE_SRC = {"kind": "movie", "tmdb": 555, "edition": "extended", "duration": 6300.0}


def test_movie_source_validation():
    doc = dict(TWO_PART)
    doc["sources"] = {"M555": MOVIE_SRC, "S01E01": {"duration": 1440.0}}
    doc["parts"] = [{"title": "p", "segments": [
        {"src": "M555", "start": 10, "end": 90, "xfade_ms": 400},
        {"src": "S01E01", "start": 0, "end": 60}]}]
    clean = tl.validate(doc)
    assert clean["sources"]["M555"]["edition"] == "extended"
    assert clean["parts"][0]["segments"][0]["xfade_ms"] == 400.0


@pytest.mark.parametrize("src,entry", [
    ("M555", {"kind": "movie", "duration": 100}),                    # no tmdb
    ("M556", {"kind": "movie", "tmdb": 555, "duration": 100}),       # key/tmdb disagree
    ("S01E01", {"kind": "movie", "tmdb": 555, "duration": 100}),     # episode key, movie kind
    ("M555", {"kind": "movie", "tmdb": -1, "duration": 100}),        # bad tmdb
    ("S01E01", {"kind": "documentary", "duration": 100}),            # bad kind
])
def test_bad_movie_sources_rejected(src, entry):
    doc = dict(TWO_PART)
    doc["sources"] = {src: entry}
    doc["parts"] = [{"title": "p", "segments": [{"src": src, "start": 0, "end": 5}]}]
    with pytest.raises(tl.TimelineError):
        tl.validate(doc)


@pytest.mark.parametrize("xfade", [-1, 0, 20_000, "fast"])
def test_bad_xfade_rejected(xfade):
    doc = dict(TWO_PART)
    doc["parts"] = [{"title": "p", "segments": [
        {"src": "S01E01", "start": 0, "end": 5, "xfade_ms": xfade}]}]
    with pytest.raises(tl.TimelineError):
        tl.validate(doc)

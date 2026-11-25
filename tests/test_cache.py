"""SourceCache: stale-while-revalidate, remembered failures, single flight, LRU."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from fanscut.cache import SourceCache
from fanscut.errors import ClientError


def test_fresh_then_stale_served_while_refreshing():
    c = SourceCache({"series": 0.05})
    n = {"v": 0}

    def load():
        n["v"] += 1
        return n["v"]

    assert c.get("series:sonarr", load) == 1
    assert c.get("series:sonarr", load) == 1  # fresh
    c._data["series:sonarr"].stamp -= 1  # age it past the TTL (time.sleep is stubbed)
    assert c.get("series:sonarr", load) == 1  # stale: served at once...
    c._pool.shutdown(wait=True)
    assert c.get("series:sonarr", load) == 2  # ...and refreshed behind the scenes


def test_failure_is_remembered_and_old_value_served():
    c = SourceCache({"series": 0.0}, fail_ttl=60, background=False)
    calls = []

    def ok():
        calls.append("ok")
        return ["Harbor Lights"]

    def down():
        calls.append("down")
        raise ClientError("Sonarr unreachable")

    assert c.get("series:sonarr", ok) == ["Harbor Lights"]
    assert c.get("series:sonarr", down) == ["Harbor Lights"]  # last good list
    assert c.get("series:sonarr", down) == ["Harbor Lights"]  # failure remembered: no new call
    assert calls == ["ok", "down"]

    fresh = SourceCache({}, fail_ttl=60)
    with pytest.raises(ClientError):
        fresh.get("movies:radarr", down)
    with pytest.raises(ClientError):
        fresh.get("movies:radarr", down)
    assert calls.count("down") == 2  # the second raise came from memory


def test_single_flight():
    c = SourceCache({})
    calls = []
    lock = threading.Lock()

    def slow():
        with lock:
            calls.append(1)
        threading.Event().wait(0.05)  # time.sleep is stubbed in tests
        return "x"

    with ThreadPoolExecutor(6) as ex:
        assert list(ex.map(lambda _i: c.get("eps:sonarr:7", slow), range(6))) == ["x"] * 6
    assert len(calls) == 1


def test_lru_cap():
    c = SourceCache({}, max_entries=2)
    for k in ("eps:a", "eps:b", "eps:c"):
        c.get(k, lambda k=k: k)
    assert c.summary()["entries"] == 2


def test_merge_does_not_edit_the_cached_lists(cfg):
    """Merging fills in art and collections; the per-source cached lists must stay as listed."""
    from fanscut.library.service import Library
    from fanscut.models import Movie

    radarr = [Movie("radarr:1", "radarr", "The Long Voyage", year=2001, file="/m/a.mkv")]
    plex = [Movie("plexmovie:9", "plex", "The Long Voyage", year=2001, file="/m/a.mkv",
                  poster="/library/metadata/9/thumb", collections=["Voyages"])]
    lib = Library(cfg)
    lib._movie_sources = lambda: ["radarr", "plexmovie"]  # type: ignore[method-assign]
    lib._movies_of = lambda src: radarr if src == "radarr" else plex  # type: ignore[method-assign]
    merged, _ = lib.list_movies()
    assert len(merged) == 1 and merged[0].collections == ["Voyages"]
    assert radarr[0].collections == [] and radarr[0].poster is None  # cached copy untouched

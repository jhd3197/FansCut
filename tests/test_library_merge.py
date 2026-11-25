"""Sonarr/Radarr and Plex on together: one entry per show and per file."""

from __future__ import annotations

from fanscut.library.service import _merge_movies, _merge_series
from fanscut.models import Movie, Series


def test_same_show_from_sonarr_and_plex_is_listed_once_as_sonarrs():
    out = _merge_series([
        Series("sonarr:1", "sonarr", "Attack on Titan", 2013, tvdb_id=267440, is_anime=True),
        Series("plex:9", "plex", "Attack on Titan", 2013, tvdb_id=267440, tmdb_id=1429),
        Series("plex:10", "plex", "Alpha Males", 2022),
    ])
    assert [s.key for s in out] == ["sonarr:1", "plex:10"]
    assert out[0].is_anime and out[0].tmdb_id == 1429


def test_show_without_ids_matches_on_title_and_year():
    out = _merge_series([Series("sonarr:1", "sonarr", "12 12 12", 2026, tvdb_id=5),
                         Series("plex:2", "plex", "12 12 12", 2026)])
    assert [s.key for s in out] == ["sonarr:1"]


def test_different_tvdb_ids_with_one_title_stay_apart():
    out = _merge_series([Series("sonarr:1", "sonarr", "Shogun", 2024, tvdb_id=1),
                         Series("plex:2", "plex", "Shogun", 2024, tvdb_id=2)])
    assert len(out) == 2


def test_same_file_from_radarr_and_plex_keeps_radarr_with_plex_collections():
    out = _merge_movies([
        Movie("radarr:1", "radarr", "The Hobbit", 2012, 49051, file=r"M:\Movies\Hobbit.mkv"),
        Movie("plexmovie:7", "plex", "The Hobbit", 2012, 49051, file="M:/Movies/Hobbit.mkv",
              duration=10950.0, collections=["The Hobbit Collection"]),
    ])
    assert [m.key for m in out] == ["radarr:1"]
    assert out[0].collections == ["The Hobbit Collection"] and out[0].duration == 10950.0


def test_a_second_edition_file_stays():
    out = _merge_movies([
        Movie("radarr:1", "radarr", "The Two Towers", 2002, 121, file="M:/a/ttt.mkv"),
        Movie("plexmovie:1", "plex", "The Two Towers", 2002, 121, file="M:/a/ttt.mkv"),
        Movie("plexmovie:2", "plex", "The Two Towers", 2002, 121, file="E:/b/ttt-ext.mkv"),
    ])
    assert [m.key for m in out] == ["radarr:1", "plexmovie:2"]


def test_radarr_path_gone_after_a_rename_gives_way_to_the_real_file(tmp_path):
    real = tmp_path / "The Hobbit (2012) {edition-Extended}.mkv"
    real.write_bytes(b"x")
    other = tmp_path / "Other Edition.mkv"
    other.write_bytes(b"x")
    out = _merge_movies([
        Movie("radarr:1", "radarr", "The Hobbit", 2012, 49051, file=str(tmp_path / "old.mkv")),
        Movie("plexmovie:2", "plex", "The Hobbit", 2012, 49051, file=str(real),
              collections=["The Hobbit"]),
        Movie("plexmovie:3", "plex", "The Hobbit", 2012, 49051, file=str(other)),
    ])
    assert [m.key for m in out] == ["plexmovie:2", "plexmovie:3"]


def test_radarr_entry_without_a_file_gives_way_to_plexs_copy():
    out = _merge_movies([
        Movie("radarr:3", "radarr", "1917", 2019, 530915),
        Movie("plexmovie:4", "plex", "1917", 2019, 530915, file="E:/Movies/1917.mkv"),
        Movie("radarr:5", "radarr", "Dune Part Three", 2026, 9),
    ])
    assert [m.key for m in out] == ["plexmovie:4", "radarr:5"]

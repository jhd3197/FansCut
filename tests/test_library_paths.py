from pathlib import Path

import pytest

from fanscut.config import PathMapping
from fanscut.library import scan
from fanscut.library.paths import map_path

MAPS = [PathMapping(remote="/tv", local="/media/tv"),
        PathMapping(remote="/tv/anime", local="/media/anime"),
        PathMapping(remote="D:\\Shows", local="/media/shows")]


@pytest.mark.parametrize(("src", "want"), [
    ("/tv/Harbor Lights/S01E01.mkv", "/media/tv/Harbor Lights/S01E01.mkv"),
    ("/tv/anime/Harbor Lights/e1.mkv", "/media/anime/Harbor Lights/e1.mkv"),  # longest prefix wins
    ("/tvx/other.mkv", "/tvx/other.mkv"),  # prefix must end at a path boundary
    ("D:\\Shows\\Harbor Lights\\e1.mkv", "/media/shows/Harbor Lights/e1.mkv"),
    ("/elsewhere/a.mkv", "/elsewhere/a.mkv"),
])
def test_map_path(src, want):
    assert map_path(src, MAPS) == want


def test_map_path_exact_prefix_and_no_mappings():
    assert map_path("/tv", MAPS) == "/media/tv"
    assert map_path("/tv/a.mkv", []) == "/tv/a.mkv"


@pytest.mark.parametrize(("name", "want"), [
    ("Harbor Lights - S01E05 - Storm.mkv", (1, 5, False)),
    ("harbor.lights.s02e11.1080p.mkv", (2, 11, False)),
    ("Harbor Lights 1x07.mp4", (1, 7, False)),
    ("[Grp] Harbor Lights - 012 [1080p].mkv", (1, 12, True)),
    ("Harbor Lights E104.mkv", (1, 104, True)),
    ("Harbor Lights ep03.mkv", (1, 3, True)),
    ("notes.mkv", None),
])
def test_parse_episode(name, want):
    assert scan.parse_episode(name) == want


def test_clean_title():
    assert scan.clean_title("Harbor Lights (2020)") == ("Harbor Lights", 2020)
    assert scan.clean_title("Harbor Lights") == ("Harbor Lights", None)


def make_tree(root: Path) -> None:
    (root / "Harbor Lights (2020)" / "Season 1").mkdir(parents=True)
    for n in (1, 2, 3):
        (root / "Harbor Lights (2020)" / "Season 1" / f"Harbor Lights - S01E{n:02d}.mkv").touch()
    (root / "Harbor Lights (2020)" / "readme.txt").touch()
    (root / "Tide Pool").mkdir()
    for n in (1, 2):
        (root / "Tide Pool" / f"Tide Pool - {n:03d}.mkv").touch()
    (root / "Empty").mkdir()
    (root / ".hidden").mkdir()


def test_scan_folder(tmp_path):
    make_tree(tmp_path)
    series = scan.list_folder_series(tmp_path)
    assert [(s.key, s.title, s.year, s.episode_count) for s in series] == [
        ("folder:Harbor Lights (2020)", "Harbor Lights", 2020, 3),
        ("folder:Tide Pool", "Tide Pool", None, 2),
    ]
    eps = scan.scan_series_dir(tmp_path / "Tide Pool")
    assert [(e.season, e.number, e.absolute) for e in eps] == [(1, 1, 1), (1, 2, 2)]
    assert eps[0].file.endswith("Tide Pool - 001.mkv")


def test_scan_missing_folder(tmp_path):
    assert scan.list_folder_series(tmp_path / "nope") == []

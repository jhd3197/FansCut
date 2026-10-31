import json

import responses

from fanscut.fillers import anifiller, jikan_flags, labels
from fanscut.models import Episode
from tests.conftest import FIXTURES

URL = "https://raw.example.test/anifiller.min.json"


def eps(*pairs, absolute=True):
    return [Episode(season=s, number=n, absolute=(i + 1 if absolute else None))
            for i, (s, n) in enumerate(pairs)]


def test_load_fixture_dir_maps_types():
    idx = anifiller.load_dir(FIXTURES / "fillers")
    show = idx.find(900001, None)
    assert show is not None and idx.find(None, 800001) is show
    assert show.labels == {1: "canon", 2: "filler", 3: "mixed", 4: "canon", 5: "filler", 6: "canon"}
    assert idx.find(1, 2) is None and (idx.files, idx.shows) == (1, 1)


def test_loader_accepts_several_shapes_and_skips_bad_files(tmp_path):
    show = {"title": "A", "mal_id": 1, "anilist_id": 11, "episodes": [{"episode": 1, "type": "filler"}]}
    other = {**show, "mal_id": 2, "anilist_id": 22}
    (tmp_path / "single.json").write_text(json.dumps(show))
    (tmp_path / "list.json").write_text(json.dumps([other]))
    (tmp_path / "keyed.json").write_text(json.dumps({"x": {**show, "mal_id": 3, "anilist_id": 33}}))
    (tmp_path / "broken.json").write_text("{nope")
    (tmp_path / "ignored.txt").write_text("[]")
    idx = anifiller.load_dir(tmp_path)
    assert sorted(idx.by_mal) == [1, 2, 3] and idx.files == 3


def test_unknown_types_are_dropped(tmp_path):
    (tmp_path / "s.json").write_text(json.dumps({"mal_id": 5, "episodes": [
        {"episode": 1, "type": "mystery"}, {"episode": 2, "type": "filler"}, {"episode": "x", "type": "filler"}]}))
    assert anifiller.load_dir(tmp_path).by_mal[5].labels == {2: "filler"}


def test_missing_dir_is_empty(tmp_path):
    assert anifiller.load_dir(tmp_path / "none").shows == 0


@responses.activate
def test_refresh_writes_cache_file(tmp_path):
    doc = json.loads((FIXTURES / "fillers" / "harbor_lights_snapshot.json").read_text())
    responses.add(responses.GET, URL, json=doc)
    res = anifiller.refresh(tmp_path / "fresh", URL)
    assert res["ok"] and res["shows"] == 1
    assert anifiller.load_dir(tmp_path / "fresh").find(900001, None)


@responses.activate
def test_refresh_failure_keeps_old_copy_and_never_raises(tmp_path):
    (tmp_path / anifiller.CACHE_NAME).write_text('{"shows": []}')
    responses.add(responses.GET, URL, status=500)
    assert not anifiller.refresh(tmp_path, URL)["ok"]
    responses.replace(responses.GET, URL, body="not json")
    assert not anifiller.refresh(tmp_path, URL)["ok"]
    responses.replace(responses.GET, URL, json={"unrelated": 1})
    assert not anifiller.refresh(tmp_path, URL)["ok"]
    assert (tmp_path / anifiller.CACHE_NAME).read_text() == '{"shows": []}'


def test_catalogue_numbers_prefer_absolute_then_count_regular_seasons():
    e = [Episode(0, 1), Episode(1, 1, absolute=1), Episode(1, 2), Episode(2, 1)]
    nums = labels.catalogue_numbers(e)
    assert nums["S00E01"] is None
    assert nums["S01E01"] == 1
    assert nums["S01E02"] == 2  # running count, since Sonarr gave no absolute number
    assert nums["S02E01"] == 3


def test_resolve_source_order():
    e = eps((1, 1), (1, 2), (1, 3), (1, 4))
    numbers = {x.id: x.absolute for x in e}
    res = labels.resolve(
        e, numbers,
        anifiller={1: "canon", 2: "filler"},
        jikan={2: {"filler": False, "recap": False}, 3: {"filler": True, "recap": False}},
        overrides={"S01E01": "filler"})
    assert [(r.label, r.source) for r in res.values()] == [
        ("filler", "override"), ("filler", "anifiller"), ("filler", "jikan"), ("unknown", "none")]


def test_jikan_recap_counts_as_filler_and_unflagged_as_canon():
    e = eps((1, 1), (1, 2))
    res = labels.resolve(e, {x.id: x.absolute for x in e}, None,
                         {1: {"filler": False, "recap": True}, 2: {"filler": False, "recap": False}}, {})
    assert (res["S01E01"].label, res["S01E02"].label) == ("filler", "canon")


def test_specials_have_no_number_so_stay_unknown():
    e = [Episode(0, 1)]
    res = labels.resolve(e, labels.catalogue_numbers(e), {1: "canon"}, None, {})
    assert res["S00E01"].label == "unknown"


@responses.activate
def test_jikan_flags_fetch_none_when_nothing_flagged(db):
    from fanscut.catalogues.http import CachedHttp, RateLimiter
    http = CachedHttp(db, 0, limiter=RateLimiter(0, sleep=lambda _s: None))
    base = "https://api.jikan.moe/v4/anime/9/episodes"
    responses.add(responses.GET, base, json={"data": [{"mal_id": 1, "filler": False, "recap": False}],
                                             "pagination": {"has_next_page": False}})
    assert jikan_flags.fetch(http, 9) is None
    responses.replace(responses.GET, base, status=500)
    assert jikan_flags.fetch(CachedHttp(db, 0, offline=True), 10) is None

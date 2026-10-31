import pytest
import requests
import responses

from fanscut.catalogues import anilist, jikan
from fanscut.catalogues import match as cmatch
from fanscut.catalogues.http import CachedHttp, RateLimiter
from fanscut.errors import ClientError
from fanscut.models import Series

URL = "https://example.test/api"


def make_http(db, **kw):
    kw.setdefault("limiter", RateLimiter(0, sleep=lambda _s: None))
    return CachedHttp(db, 0, **kw)


def test_rate_limiter_spaces_calls():
    now = [0.0]
    slept = []
    rl = RateLimiter(1.0, clock=lambda: now[0], sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)))
    rl.wait()
    now[0] += 0.25
    rl.wait()
    assert slept == [pytest.approx(0.75)]


@responses.activate
def test_cache_hit_avoids_second_request(db):
    responses.add(responses.GET, URL, json={"n": 1})
    http = make_http(db)
    assert http.request("GET", URL, params={"a": 1}) == {"n": 1}
    assert http.request("GET", URL, params={"a": 1}) == {"n": 1}
    assert len(responses.calls) == 1
    http.request("GET", URL, params={"a": 2})
    assert len(responses.calls) == 2


@responses.activate
def test_stale_entry_served_when_network_fails(db):
    responses.add(responses.GET, URL, json={"n": 1})
    http = make_http(db, ttl_days=0)  # everything is immediately stale
    http.request("GET", URL)
    responses.replace(responses.GET, URL, body=requests.ConnectionError("down"))
    assert http.request("GET", URL) == {"n": 1}


@responses.activate
def test_failure_without_cache_raises_client_error(db):
    responses.add(responses.GET, URL, status=500)
    with pytest.raises(ClientError):
        make_http(db).request("GET", URL)


@responses.activate
def test_offline_never_touches_network(db):
    http = make_http(db, offline=True)
    with pytest.raises(ClientError, match="offline"):
        http.request("GET", URL)
    assert not responses.calls


@responses.activate
def test_429_is_retried(db, monkeypatch):
    monkeypatch.setattr("fanscut.catalogues.http.time.sleep", lambda _s: None)
    responses.add(responses.GET, URL, status=429, headers={"Retry-After": "1"})
    responses.add(responses.GET, URL, json={"ok": 1})
    assert make_http(db).request("GET", URL) == {"ok": 1}


@responses.activate
def test_post_body_is_part_of_the_cache_key(db):
    responses.add(responses.POST, URL, json={"n": 1})
    http = make_http(db)
    http.request("POST", URL, body={"q": "a"})
    http.request("POST", URL, body={"q": "a"})
    http.request("POST", URL, body={"q": "b"})
    assert len(responses.calls) == 2


ANILIST_REPLY = {"data": {"Page": {"media": [
    {"id": 800001, "idMal": 900001, "format": "TV", "episodes": 6, "seasonYear": 2020,
     "title": {"romaji": "Minato no Akari", "english": "Harbor Lights", "native": None},
     "synonyms": ["Harbour Lights"]},
    {"id": 800002, "idMal": None, "format": "MOVIE", "episodes": 1, "seasonYear": 2022,
     "title": {"romaji": "Tide Pool", "english": None, "native": None}, "synonyms": []},
]}}}

JIKAN_SEARCH = {"data": [
    {"mal_id": 900001, "title": "Minato no Akari", "title_english": "Harbor Lights", "year": 2020,
     "episodes": 6, "type": "TV", "titles": [{"title": "Harbor Lights"}]},
    {"mal_id": 900009, "title": "Lantern Festival", "title_english": None, "year": None,
     "aired": {"prop": {"from": {"year": 2015}}}, "episodes": 12, "type": "TV", "titles": []},
]}


@responses.activate
def test_anilist_search_parses(db):
    responses.add(responses.POST, anilist.URL, json=ANILIST_REPLY)
    res = anilist.search(make_http(db), "harbor lights")
    assert res[0]["anilist_id"] == 800001 and res[0]["mal_id"] == 900001
    assert "Harbour Lights" in res[0]["names"]
    assert res[1]["mal_id"] is None and res[1]["title"] == "Tide Pool"


@responses.activate
def test_jikan_search_parses(db):
    responses.add(responses.GET, f"{jikan.BASE}/anime", json=JIKAN_SEARCH)
    res = jikan.search(make_http(db), "harbor lights")
    assert res[0]["mal_id"] == 900001 and res[0]["year"] == 2020
    assert res[1]["year"] == 2015


@responses.activate
def test_jikan_episode_flags_paginate(db):
    base = f"{jikan.BASE}/anime/900001/episodes"
    responses.add(responses.GET, base, json={"data": [
        {"mal_id": 1, "filler": False, "recap": False}, {"mal_id": 2, "filler": True, "recap": False}],
        "pagination": {"has_next_page": True}})
    responses.add(responses.GET, base, json={"data": [
        {"mal_id": 3, "filler": False, "recap": True}], "pagination": {"has_next_page": False}})
    flags = jikan.episode_flags(make_http(db), 900001)
    assert flags == {1: {"filler": False, "recap": False}, 2: {"filler": True, "recap": False},
                     3: {"filler": False, "recap": True}}
    assert "page=2" in responses.calls[1].request.url


@responses.activate
def test_candidates_merge_on_mal_id_and_rank(db):
    responses.add(responses.POST, anilist.URL, json=ANILIST_REPLY)
    responses.add(responses.GET, f"{jikan.BASE}/anime", json=JIKAN_SEARCH)
    series = Series("sonarr:7", "sonarr", "Harbor Lights", year=2020)
    cands = cmatch.candidates(make_http(db), series)
    top = cands[0]
    assert top["mal_id"] == 900001 and top["anilist_id"] == 800001
    assert top["source"] == "anilist+jikan" and top["score"] >= cmatch.AUTO_THRESHOLD
    assert [c["score"] for c in cands] == sorted((c["score"] for c in cands), reverse=True)
    assert cmatch.auto_match(make_http(db), series)["mal_id"] == 900001


@responses.activate
def test_auto_match_declines_weak_matches(db):
    responses.add(responses.POST, anilist.URL, json=ANILIST_REPLY)
    responses.add(responses.GET, f"{jikan.BASE}/anime", json=JIKAN_SEARCH)
    assert cmatch.auto_match(make_http(db), Series("x", "sonarr", "Completely Unrelated Name")) is None


def test_candidates_survive_both_catalogues_failing(db):
    http = make_http(db, offline=True)
    assert cmatch.candidates(http, Series("x", "sonarr", "Harbor Lights")) == []


def test_score_uses_alt_titles_and_penalises_year():
    cand = {"names": ["Minato no Akari"], "year": 2020}
    plain = Series("k", "s", "Harbor Lights", year=2020)
    alt = Series("k", "s", "Harbor Lights", year=2020, alt_titles=["Minato no Akari"])
    assert cmatch.score(alt, cand) > cmatch.score(plain, cand)
    assert cmatch.score(Series("k", "s", "Minato no Akari", year=2010), cand) < cmatch.score(
        Series("k", "s", "Minato no Akari", year=2020), cand)

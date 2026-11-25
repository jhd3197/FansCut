"""Community catalogue: index validation, availability, install by repo URL, updates."""

from __future__ import annotations

import json

import pytest
import responses

from fanscut import community
from fanscut.core import Core

SHA1 = "a" * 40
SHA2 = "b" * 40
API = "https://api.github.com/repos/jhd3197/took"
INDEX = "https://raw.githubusercontent.com/jhd3197/fanscut-community/main/index.json"

TOOK = {"schema": 2, "kind": "timeline", "id": "jhd3197/took", "version": "0.1.0",
        "title": "Took", "category": "Fantasy", "tagline": "The Hobbit, as one film",
        "series": {"title": "Took"}, "poster": "https://example.org/took.jpg",
        "sources": {"AUJ": {"kind": "movie", "tmdb": 555, "title": "Tide Pool"}},
        "parts": [{"title": "Took", "segments": [{"src": "AUJ", "start": 1.0, "end": 9.0}]}],
        "credits": {"author": "jhd3197", "license": "CC-BY-4.0"}}

ENTRY = {"name": "Took", "repo": "https://github.com/jhd3197/took", "kind": "merge",
         "category": "Fantasy", "tagline": "The Hobbit, as one film",
         "sources": [{"tmdb": 555, "title": "Tide Pool"},
                     {"tmdb": 120, "title": "The Fellowship of the Ring", "optional": True}],
         "variants": ["Book cut", "Middle-earth cut"]}


def test_parse_repo():
    assert community.parse_repo("https://github.com/jhd3197/took.git").slug == "jhd3197/took"
    for bad in ("http://github.com/a/b", "https://gitlab.com/a/b", "https://github.com/a",
                "https://github.com/a/b/../c", "file:///etc/passwd"):
        with pytest.raises(community.CommunityError):
            community.parse_repo(bad)


def test_index_drops_bad_entries_only():
    entries = community.validate_index({"schema": 1, "cuts": [
        ENTRY, {"name": "x", "repo": "https://evil.example/x"},
        {**ENTRY, "poster": "http://insecure/x.jpg"}]})
    assert [e["name"] for e in entries] == ["Took"]
    with pytest.raises(community.CommunityError):
        community.validate_index({"cuts": []})


def test_availability_names_missing_and_optional():
    e = community.validate_index({"schema": 1, "cuts": [ENTRY]})[0]
    assert community.availability(e, {555}, set())[0] == "ready"
    status, missing = community.availability(e, set(), set())
    assert status == "missing" and missing[0]["src"] == "Tide Pool"
    assert "optional" in missing[1]["reason"]


def _core(client) -> Core:
    return client.app_ref.state.core


def _mock_repo(rsps, sha=SHA1, doc=TOOK):
    rsps.add(responses.GET, API, json={"default_branch": "dev"})
    rsps.add(responses.GET, f"{API}/commits/dev", json={"sha": sha})
    rsps.add(responses.GET, f"{API}/contents/fanscut.json?ref={sha}", body=json.dumps(doc))
    rsps.add(responses.GET, "https://example.org/took.jpg", body=b"\xff\xd8jpeg",
             content_type="image/jpeg")


def test_install_records_sha_poster_and_updates(client, sonarr_mock, tmp_path):
    core = _core(client)
    _mock_repo(sonarr_mock)
    r = client.post("/api/cuts/install", json={"url": "https://github.com/jhd3197/took"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["sha"] == SHA1 and out["ref"] == "dev" and out["poster"]
    tl = client.get(f"/api/timelines/{out['id']}").json()
    assert tl["doc"]["sources"]["AUJ"]["note"] == "Tide Pool"
    row = core.db.one("SELECT * FROM installs WHERE timeline_id = ?", (out["id"],))
    assert row["repo"] == "jhd3197/took" and row["poster"].endswith(".jpg")

    # same SHA: nothing changes
    sonarr_mock.add(responses.GET, f"{API}/commits/dev", json={"sha": SHA1})
    ups = client.get("/api/cuts/updates").json()["installs"]
    assert ups[0]["update"] is False

    # the repo moved on: update pulls the new recipe into the same timeline
    new = {**TOOK, "title": "Took (v2)",
           "parts": [{"title": "Took", "segments": [{"src": "AUJ", "start": 2.0, "end": 9.0}]}]}
    sonarr_mock.add(responses.GET, f"{API}/commits/dev", json={"sha": SHA2})
    sonarr_mock.add(responses.GET, f"{API}/contents/fanscut.json?ref={SHA2}",
                    body=json.dumps(new))
    ups = client.get("/api/cuts/updates").json()["installs"]
    assert ups[0]["update"] is True
    assert ups[0]["summary"] == "1 film re-timed, renamed to “Took (v2)”"
    up = client.post(f"/api/cuts/{out['id']}/update").json()
    assert up["id"] == out["id"] and up["changed"] and up["sha"] == SHA2
    assert up["summary"].startswith("1 film re-timed")
    assert client.get(f"/api/timelines/{out['id']}").json()["title"] == "Took (v2)"


def test_install_rejects_non_github(client):
    r = client.post("/api/cuts/install", json={"url": "https://example.org/x"})
    assert r.status_code == 400


def test_private_repo_names_the_token(client, sonarr_mock):
    sonarr_mock.add(responses.GET, API, status=404)
    r = client.post("/api/cuts/install", json={"url": "https://github.com/jhd3197/took"})
    assert r.status_code == 400 and "GitHub token" in r.json()["detail"]


def test_catalogue_rates_entries(client, sonarr_mock):
    sonarr_mock.add(responses.GET, INDEX, json={"schema": 1, "cuts": [ENTRY]})
    cuts = client.get("/api/catalogue").json()["cuts"]
    assert cuts[0]["id"] == "repo:jhd3197/took" and cuts[0]["status"] == "missing"
    assert cuts[0]["missing"][0]["src"] == "Tide Pool"


def test_github_token_is_masked(client):
    core = _core(client)
    core.cfg.community.github_token = "ghp_secret"
    s = client.get("/api/settings").json()
    assert "ghp_secret" not in json.dumps(s)


def test_change_summary_words():
    def doc(*segs, parts=1, variants=()):
        return {"title": "T", "sources": {"S01E01": {}, "S01E02": {}},
                "variants": [{"id": v} for v in variants],
                "parts": [{"segments": list(segs)}] * parts}

    a = {"src": "S01E01", "start": 0, "end": 10}
    b = {"src": "S01E02", "start": 0, "end": 10}
    moved = {**a, "end": 12}
    assert community.change_summary(doc(a, b), doc(moved, {**b, "start": 1})) == \
        "2 episodes re-timed"
    assert community.change_summary(doc(a), doc(a, b)) == "1 scene added"
    assert community.change_summary(doc(a, b), doc(b, a)) == "2 scenes replaced"
    assert community.change_summary(doc(a), doc(a, variants=["x"])) == "1 new variant"
    assert community.change_summary(doc(a), doc(a)).startswith("details changed")


def test_index_can_be_a_local_file(client, tmp_path):
    """A draft index on disk, to try entries before they reach fanscut-community."""
    core = _core(client)
    f = tmp_path / "index.json"
    f.write_text(json.dumps({"schema": 1, "cuts": [ENTRY]}))
    for url in (str(f), f.as_uri()):
        core.cfg.community.index_url = url
        core._index_cache = None
        cuts = client.get("/api/catalogue").json()["cuts"]
        assert [c["id"] for c in cuts] == ["repo:jhd3197/took"], url
    assert community.local_index_path(INDEX) is None


def test_default_index_is_fanscut_community():
    from fanscut.config import CommunityCfg

    assert CommunityCfg().index_url == INDEX
    assert "fanscut-community/main/index.json" in INDEX

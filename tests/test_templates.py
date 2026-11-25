"""Cut templates: files, timeline, optional private repo push."""

from __future__ import annotations

import json

import pytest
import responses

from fanscut import templates
from tests.test_api import matched  # noqa: F401  (fixture)
from tests.test_suggestions import took_library  # noqa: F401  (fixture)


def test_alias_is_short_unique_and_legal():
    used: set[str] = set()
    assert templates.alias("The Desolation of Smaug", used) == "DS"
    assert templates.alias("Desolation Smaug", used) == "DS2"
    assert templates.alias("1917", used).startswith("M")


def _keys(client):
    return [m["key"] for m in client.get("/api/library/movies").json()["movies"]]


def test_trilogy_merge_preview_and_create(client, took_library, tmp_path):  # noqa: F811
    body = {"title": "Took", "movies": _keys(client)}
    files = client.post("/api/templates/trilogy-merge/preview", json=body).json()["files"]
    assert set(files) == {"README.md", "fanscut.json", "outline.md"}
    doc = json.loads(files["fanscut.json"])
    assert len(doc["parts"]) == 1 and len(doc["parts"][0]["segments"]) == 3
    assert "No video, audio, subtitles" in files["README.md"]
    r = client.post("/api/templates/trilogy-merge/create", json=body).json()
    assert client.get(f"/api/timelines/{r['id']}").json()["title"] == "Took"
    assert sorted(r["files"]) == ["README.md", "fanscut.json", "outline.md"]


def test_watch_order_is_one_part_per_film(client, took_library):  # noqa: F811
    doc = json.loads(client.post("/api/templates/watch-order/preview",
                                 json={"movies": _keys(client)}).json()["files"]["fanscut.json"])
    assert len(doc["parts"]) == 3


def test_merge_needs_two_films(client, took_library):  # noqa: F811
    r = client.post("/api/templates/trilogy-merge/preview", json={"movies": _keys(client)[:1]})
    assert r.status_code == 400


def test_canon_template_keeps_canon_and_mixed(matched):  # noqa: F811
    doc = json.loads(matched.post("/api/templates/canon/preview",
                                  json={"series_key": "sonarr:7"}).json()["files"]["fanscut.json"])
    labels = {e["id"]: e["label"] for e in
              matched.get("/api/series/sonarr:7/episodes").json()["episodes"]}
    assert doc["sources"] and all(labels[k] in ("canon", "mixed") for k in doc["sources"])


def test_push_needs_token_then_creates_private_repo(client, took_library):  # noqa: F811
    body = {"movies": _keys(client), "push": {"name": "Took", "private": True}}
    r = client.post("/api/templates/trilogy-merge/create", json=body)
    assert r.status_code == 400 and "token" in r.json()["detail"]
    client.app_ref.state.core.cfg.community.github_token = "t"
    took_library.add(responses.POST, "https://api.github.com/user/repos",
                     json={"full_name": "me/took"})
    for f in ("fanscut.json", "README.md", "outline.md"):
        took_library.add(responses.PUT, f"https://api.github.com/repos/me/took/contents/{f}",
                         json={"commit": {"sha": "c" * 40}})
    out = client.post("/api/templates/trilogy-merge/create", json=body).json()
    assert out["repo"] == "https://github.com/me/took"
    sent = json.loads(took_library.calls[-4].request.body)  # the create-repo call
    assert sent == {**sent, "name": "took", "private": True}


def test_unknown_template(client):
    assert client.post("/api/templates/nope/preview", json={}).status_code == 400


@pytest.mark.parametrize("tid", [t["id"] for t in templates.TEMPLATES])
def test_templates_listed(client, tid):
    assert tid in [t["id"] for t in client.get("/api/templates").json()["templates"]]

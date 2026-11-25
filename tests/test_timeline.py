"""Timeline recipes (schema 2, kind=timeline): validation, import, missing sources."""

import pytest

from fanscut.timeline import recipe as tl
from tests.test_api import E

TWO_PART = {
    "schema": 2,
    "kind": "timeline",
    "title": "Harbor Lights — Chronological",
    "series": {"title": "Harbor Lights", "tvdb_id": 111},
    "sources": {
        "S01E01": {"duration": 1440.0},
        "S01E04": {"duration": 1440.0, "note": "the dock fight"},
        "S00E01": {"duration": 1450.0, "note": "OVA"},
    },
    "parts": [
        {"title": "Before the storm", "segments": [
            {"src": "S01E04", "start": 312.4, "end": 498.0},
            {"src": "S00E01", "start": 10.0, "end": 100.0},
        ]},
        {"title": "The storm", "segments": [
            {"src": "S01E01", "start": 0.0, "end": 1440.0},
        ]},
    ],
    "credits": {"author": "someone", "license": "CC-BY-4.0"},
}


def test_validate_round_trip():
    clean = tl.validate(TWO_PART)
    assert clean["title"] == "Harbor Lights — Chronological"
    assert clean["parts"][0]["segments"][0] == {"src": "S01E04", "start": 312.4, "end": 498.0}
    assert clean["credits"] == {"author": "someone", "license": "CC-BY-4.0"}


def test_validate_anchors():
    import base64

    doc = {**TWO_PART, "parts": [{"title": "p", "segments": [
        {"src": "S01E01", "start": 1, "end": 2,
         "anchors": {"start": {"a": "a1:" + base64.b64encode(b"x" * 390).decode(),
                               "v": "v1:" + base64.b64encode(b"y" * 256).decode()}}}]}]}
    clean = tl.validate(doc)
    assert clean["parts"][0]["segments"][0]["anchors"]["start"]["a"].startswith("a1:")


def _bad(**over):
    doc = {**TWO_PART, **over}
    return doc


@pytest.mark.parametrize("doc", [
    [], {"schema": 1, "kind": "timeline"}, _bad(schema=3), _bad(kind="series"),
    _bad(title=""), _bad(title="x" * 300), _bad(title="a\nb"),
    _bad(series={}), _bad(series={"title": "x", "tvdb_id": -1}),
    _bad(sources={}), _bad(sources={"E4": {"duration": 10}}),
    _bad(sources={"S01E01": {"duration": -5}}),
    _bad(parts=[]), _bad(parts=[{"title": "p", "segments": []}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S09E99", "start": 0, "end": 5}]}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S01E01", "start": -1, "end": 5}]}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S01E01", "start": 5, "end": 5}]}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S01E01", "start": 0, "end": 99999}]}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S01E01", "start": 0, "end": float("nan")}]}]),
    _bad(parts=[{"title": "p", "segments": [{"src": "S01E01", "start": 0, "end": 5,
                                             "anchors": {"start": {"a": "a2:nope"}}}]}]),
    _bad(credits={"x": ["list"]}),
])
def test_invalid_timelines_rejected(doc):
    with pytest.raises(tl.TimelineError):
        tl.validate(doc)


def test_caps():
    many_parts = [dict(p, title=f"p{i}") for i, p in enumerate(TWO_PART["parts"] * 150)]
    with pytest.raises(tl.TimelineError, match="parts"):
        tl.validate(_bad(parts=many_parts))
    huge = {"title": "p", "segments": [{"src": "S01E01", "start": i * 0.2, "end": i * 0.2 + 0.1}
                                       for i in range(6000)]}
    with pytest.raises(tl.TimelineError, match="segments"):
        tl.validate(_bad(parts=[huge]))


def test_missing_sources_report_parts():
    clean = tl.validate(TWO_PART)
    missing = tl.missing_sources(clean, {"S01E01", "S01E04"})
    assert missing == [{"src": "S00E01", "parts": [1], "note": "OVA"}]
    assert tl.missing_sources(clean, {"S01E01", "S01E04", "S00E01"}) == []


def test_timeline_api_crud_and_missing(client):
    r = client.post(f"{E}/timelines", json=TWO_PART)
    assert r.status_code == 201
    tid = r.json()["id"]
    # the mocked library has S01E01-S01E06; S00E01 is missing and reported with its part
    assert r.json()["missing"] == [{"src": "S00E01", "parts": [1], "note": "OVA"}]
    assert r.json()["parts"] == 2 and r.json()["synced"] is False

    got = client.get(f"/api/timelines/{tid}").json()
    assert got["doc"]["title"] == TWO_PART["title"]
    listed = client.get(f"{E}/timelines").json()["timelines"]
    assert [t["id"] for t in listed] == [tid]

    smaller = {**TWO_PART, "title": "Renamed", "sources": {"S01E01": {"duration": 1440.0}},
               "parts": [TWO_PART["parts"][1]]}
    r = client.put(f"/api/timelines/{tid}", json=smaller)
    assert r.json()["title"] == "Renamed" and r.json()["missing"] == []
    assert client.delete(f"/api/timelines/{tid}").json() == {"ok": True}
    assert client.get(f"/api/timelines/{tid}").status_code == 404


def test_timeline_api_rejects_garbage_and_oversize(client):
    assert client.post(f"{E}/timelines", json={"schema": 2, "kind": "nope"}).status_code == 400
    assert client.post(f"{E}/timelines", json={"schema": 1}).status_code == 400
    big = dict(TWO_PART)
    big["credits"] = {"padding": "x" * 2_000_000}
    assert client.post(f"{E}/timelines", json=big).status_code == 400


def test_series_recipe_endpoint_points_timelines_away(client):
    r = client.post(f"{E}/recipe", json=TWO_PART)
    assert r.status_code == 400 and "timelines" in r.json()["detail"]


def test_cli_recipe_validate(tmp_path, capsys):
    from fanscut import cli
    from fanscut.config import Config

    f = tmp_path / "t.json"
    f.write_text(__import__("json").dumps(TWO_PART))
    assert cli.main(["recipe", "validate", "--file", str(f)]) == 0
    assert "2 part(s), 3 segment(s), 3 source(s)" in capsys.readouterr().out
    f.write_text("{broken")
    assert cli.main(["recipe", "validate", "--file", str(f)]) == 1
    assert "invalid JSON" in capsys.readouterr().err
    assert Config()  # keep import honest


def test_timeline_sync_job_and_report(client, monkeypatch):
    """Sync matches every boundary and stores a per-source report."""
    import fanscut.timeline.anchors as anchors_mod

    doc = dict(TWO_PART)
    doc["sources"] = {"S01E01": {"duration": 1440.0}, "S01E04": {"duration": 1440.0}}
    doc["parts"] = [{"title": "p1", "segments": [
        {"src": "S01E01", "start": 100.0, "end": 200.0,
         "anchors": {"start": {"a": "a1:" + "QUJD" * 100}, "end": {"a": "a1:" + "QUJD" * 100}}}]}]
    tid = client.post(f"{E}/timelines", json=doc).json()["id"]

    seen = []

    def fake_find(ff, fp, path, t, anch, **kw):
        seen.append((path, t))
        return {"time": t + 2.3, "offset": 2.3, "method": "audio", "confidence": 0.9}

    monkeypatch.setattr(anchors_mod, "find_in_file", fake_find)
    # the mocked Sonarr files don't exist on disk; pretend they do
    from pathlib import Path
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    job = client.post(f"/api/timelines/{tid}/sync").json()
    core = client.app_ref.state.core
    core.worker.run_pending()
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["status"] == "done" and done["result"]["boundaries"] == 2

    rep = client.get(f"/api/timelines/{tid}/sync").json()
    assert rep["synced"] is True
    src = rep["sources"]["S01E01"]
    assert src["matched"] is True and src["strategy"] == "constant"
    assert [b["corrected"] for b in src["boundaries"]] == [102.3, 202.3]
    # an edit invalidates the sync
    client.put(f"/api/timelines/{tid}", json=doc)
    assert client.get(f"/api/timelines/{tid}/sync").json()["synced"] is False


def test_timeline_sync_reports_missing_files(client, monkeypatch):
    tid = client.post(f"{E}/timelines", json=TWO_PART).json()["id"]  # S00E01 missing
    client.post(f"/api/timelines/{tid}/sync")
    core = client.app_ref.state.core
    from pathlib import Path
    monkeypatch.setattr(Path, "is_file", lambda self: True)
    core.worker.run_pending()
    rep = client.get(f"/api/timelines/{tid}/sync").json()
    assert "S00E01" in rep["unmatched_sources"]
    assert rep["sources"]["S00E01"]["reason"] == "no file in this library"

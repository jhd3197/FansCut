import pytest
import requests
import responses

from fanscut.clients.base import BaseClient
from fanscut.clients.plex import PlexClient
from fanscut.clients.sonarr import SonarrClient
from fanscut.errors import ClientError

S = "http://sonarr.test:8989"
P = "http://plex.test:32400"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr("fanscut.clients.base.time.sleep", lambda _s: None)


@responses.activate
def test_sonarr_calls_and_key_header():
    responses.add(responses.GET, f"{S}/api/v3/series", json=[{"id": 1}])
    responses.add(responses.GET, f"{S}/api/v3/episode", json=[{"id": 2}])
    responses.add(responses.GET, f"{S}/api/v3/episodefile", json=[{"id": 3}])
    c = SonarrClient(S + "/", "k")
    assert c.list_series() == [{"id": 1}]
    assert c.episodes(7) == [{"id": 2}]
    assert c.episode_files(7) == [{"id": 3}]
    assert responses.calls[0].request.headers["X-Api-Key"] == "k"
    assert "seriesId=7" in responses.calls[1].request.url
    assert "seriesId=7" in responses.calls[2].request.url


@responses.activate
def test_plex_token_in_header_not_url():
    responses.add(responses.GET, f"{P}/library/sections", json={"MediaContainer": {"Directory": [
        {"key": "1", "type": "show", "title": "TV"}, {"key": "2", "type": "movie", "title": "Films"}]}})
    responses.add(responses.GET, f"{P}/library/sections/1/all", json={"MediaContainer": {
        "Metadata": [{"ratingKey": "55", "title": "Harbor Lights"}]}})
    responses.add(responses.GET, f"{P}/library/metadata/55/allLeaves", json={"MediaContainer": {
        "Metadata": [{"index": 1, "parentIndex": 1}]}})
    c = PlexClient(P, "tok")
    assert [s["title"] for s in c.sections()] == ["TV", "Films"]
    assert c.shows("1")[0]["ratingKey"] == "55"
    assert c.episodes("55") == [{"index": 1, "parentIndex": 1}]
    for call in responses.calls:
        assert call.request.headers["X-Plex-Token"] == "tok"
        assert "tok" not in call.request.url


@responses.activate
def test_retries_transient_then_succeeds():
    responses.add(responses.GET, f"{S}/x", status=503)
    responses.add(responses.GET, f"{S}/x", status=429, headers={"Retry-After": "1"})
    responses.add(responses.GET, f"{S}/x", json={"ok": True})
    assert BaseClient(S)._get("/x") == {"ok": True}
    assert len(responses.calls) == 3


@responses.activate
def test_gives_up_after_retries():
    responses.add(responses.GET, f"{S}/x", status=500)
    with pytest.raises(ClientError) as ei:
        BaseClient(S)._get("/x")
    assert ei.value.status == 500 and len(responses.calls) == 3


@responses.activate
@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_not_retried(status):
    responses.add(responses.GET, f"{S}/x", status=status)
    with pytest.raises(ClientError, match="credentials"):
        BaseClient(S)._get("/x")
    assert len(responses.calls) == 1


@responses.activate
def test_connection_error_and_bad_json():
    responses.add(responses.GET, f"{S}/down", body=requests.ConnectionError("boom"))
    with pytest.raises(ClientError, match="unreachable"):
        BaseClient(S)._get("/down")
    responses.add(responses.GET, f"{S}/html", body="<html>", status=200)
    with pytest.raises(ClientError, match="non-JSON"):
        BaseClient(S)._get("/html")


@responses.activate
def test_errors_never_contain_credentials():
    url = "http://plex.test:32400/"
    responses.add(responses.GET, url, body=requests.ConnectionError(f"failed {url}?X-Plex-Token=sekrit"))
    with pytest.raises(ClientError) as ei:
        PlexClient("http://plex.test:32400", "sekrit").identity()
    assert "sekrit" not in str(ei.value)

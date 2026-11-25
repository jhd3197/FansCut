"""Sonarr webhook: test event, gating, and auto detect+render queueing."""

from tests.conftest import SERIES
from tests.test_api import core_of


def download_event(series_id=7, season=1):
    return {"eventType": "Download",
            "series": {"id": series_id, "title": "Harbor Lights"},
            "episodes": [{"seasonNumber": season, "episodeNumber": 7}]}


def test_test_event_always_ok(client):
    r = client.post("/api/webhooks/sonarr", json={"eventType": "Test"})
    assert r.status_code == 200 and r.json()["action"] == "test"


def test_ignored_when_automation_off(client):
    r = client.post("/api/webhooks/sonarr", json=download_event()).json()
    assert r["action"] == "ignored" and "off" in r["reason"]


def test_grab_and_unknown_series_ignored(client, app_cfg):
    app_cfg.automation.sonarr_webhook = True
    r = client.post("/api/webhooks/sonarr", json={**download_event(), "eventType": "Grab"})
    assert r.json()["action"] == "ignored"
    r = client.post("/api/webhooks/sonarr", json=download_event(series_id=999))
    assert r.json()["action"] == "ignored" and "not in the library" in r.json()["reason"]


def test_download_queues_detect_and_render(client, app_cfg):
    app_cfg.automation.sonarr_webhook = True
    r = client.post("/api/webhooks/sonarr", json=download_event()).json()
    assert r["ok"] and r["action"] == "queued" and r["seasons"] == [1]
    jobs = [client.get(f"/api/jobs/{j}").json() for j in r["jobs"]]
    assert [j["kind"] for j in jobs] == ["detect", "render"]  # detect runs first (FIFO)
    render = jobs[-1]
    assert render["payload"]["series_key"] == SERIES and render["payload"]["seasons"] == [1]
    assert render["payload"]["keep"] is True


def test_upgrade_without_episode_list_renders_whole_series(client, app_cfg):
    app_cfg.automation.sonarr_webhook = True
    app_cfg.automation.auto_detect = False
    event = {"eventType": "Upgrade", "series": {"id": 7, "title": "Harbor Lights"}}
    r = client.post("/api/webhooks/sonarr", json=event).json()
    assert r["action"] == "queued" and r["seasons"] is None
    jobs = [client.get(f"/api/jobs/{j}").json() for j in r["jobs"]]
    assert [j["kind"] for j in jobs] == ["render"]  # no detect when auto_detect is off
    assert jobs[0]["payload"]["seasons"] is None


def test_webhook_never_raises_on_empty_payload(client, app_cfg):
    app_cfg.automation.sonarr_webhook = True
    r = client.post("/api/webhooks/sonarr", json={})
    assert r.status_code == 200 and r.json()["action"] == "ignored"
    assert core_of(client).jobs.list() == []

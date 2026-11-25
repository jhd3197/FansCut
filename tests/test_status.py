"""/healthz, /api/status, the restart flag, logs, error shapes and /api/v1."""

from __future__ import annotations

import json
import logging

from fanscut import logs
from tests.test_api import core_of


def test_healthz_checks_the_database(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_status_and_restart_flag(client):
    s = client.get("/api/status").json()
    assert s["restart_required"] is False and "queue" in s and "commit" in s
    r = client.put("/api/settings", json={"server": {"port": 7123}}).json()
    assert r["restart_required"] and r["restart_fields"] == ["server.port"]
    assert client.get("/api/status").json()["restart_fields"] == ["server.port"]


def test_api_v1_alias(client):
    assert client.get("/api/v1/status").json() == client.get("/api/status").json() | {
        "uptime_seconds": client.get("/api/v1/status").json()["uptime_seconds"]}


def test_error_shapes(client):
    r = client.get("/api/jobs/missing")
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    bad = client.post("/api/settings/test/sonarr", json={"url": 5})
    assert bad.status_code == 422 and bad.json()["code"] == "validation"
    assert isinstance(bad.json()["detail"], str) and bad.json()["errors"]


def test_logs_are_written_and_filtered(tmp_path):
    logs.JsonFormatter()
    folder = tmp_path / "logs"
    folder.mkdir()
    rows = [{"ts": "t", "level": "INFO", "logger": "x", "message": "rendered Harbor Lights"},
            {"ts": "t", "level": "ERROR", "logger": "x", "message": "ffmpeg failed"},
            {"ts": "t", "level": "DEBUG", "logger": "x", "message": "noise"}]
    (folder / logs.FILE).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    got = logs.read(tmp_path, "INFO")
    assert [e["message"] for e in got["results"]] == ["ffmpeg failed", "rendered Harbor Lights"]
    assert logs.read(tmp_path, "DEBUG", search="harbor")["page_info"]["results"] == 1
    page = logs.read(tmp_path, "DEBUG", take=1, skip=1)
    assert page["results"][0]["message"] == "ffmpeg failed" and page["page_info"]["pages"] == 3


def test_json_formatter():
    rec = logging.LogRecord("fanscut.x", logging.WARNING, __file__, 1, "hi %s", ("Mina",), None)
    assert json.loads(logs.JsonFormatter().format(rec))["message"] == "hi Mina"


def test_logs_endpoint(client):
    assert client.get("/api/logs").json()["results"] == []
    assert core_of(client)

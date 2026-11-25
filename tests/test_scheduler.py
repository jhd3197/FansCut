"""Cron parsing, due tasks, and the schedules API."""

from __future__ import annotations

import datetime as dt

import pytest

from fanscut.scheduler import Cron, CronError, Scheduler, describe


def test_cron_parsing_and_matching():
    c = Cron.parse("*/15 3 * * 1-5")
    assert c.matches(dt.datetime(2026, 11, 23, 3, 30))  # a Monday
    assert not c.matches(dt.datetime(2026, 11, 22, 3, 30))  # Sunday
    assert not c.matches(dt.datetime(2026, 11, 23, 4, 30))
    assert Cron.parse("0 5 * * *").next_after(dt.datetime(2026, 1, 1, 5, 0)) == \
        dt.datetime(2026, 1, 2, 5, 0)
    assert Cron.parse("0 0 1 * 0").matches(dt.datetime(2026, 2, 1))  # day OR weekday
    for bad in ("* * *", "61 * * * *", "*/0 * * * *", "a b c d e"):
        with pytest.raises(CronError):
            Cron.parse(bad)
    assert describe("*/10 * * * *") == "every 10 minutes"
    assert describe("0 5 * * *") == "daily at 05:00"
    assert describe("0 3 * * 0") == "Suns at 03:00"


def test_tick_runs_due_tasks_once_a_minute():
    ran = []
    now = {"t": dt.datetime(2026, 1, 1, 5, 0, 10)}
    s = Scheduler({"prune": ("p", lambda c: ran.append("prune") or {}),
                   "off": ("o", lambda c: ran.append("off") or {})},
                  lambda: {"prune": "0 5 * * *", "off": ""}, now=lambda: now["t"])
    s.run = lambda name, background=True: Scheduler.run(s, name, background=False)  # type: ignore[method-assign]
    assert s.tick() == ["prune"]
    now["t"] = dt.datetime(2026, 1, 1, 5, 0, 40)
    assert s.tick() == []  # same minute
    assert ran == ["prune"]
    d = {x["name"]: x for x in s.describe()}
    assert d["off"]["when"] == "off" and d["prune"]["next_run"] == "2026-01-02T05:00"
    assert d["prune"]["last_result"] == {}


def test_failed_task_is_recorded():
    def boom(_c):
        raise RuntimeError("Plex down")

    s = Scheduler({"t": ("t", boom)}, lambda: {})
    s.run("t", background=False)
    assert s.describe()[0]["last_error"] == "Plex down" and not s.describe()[0]["running"]


def test_schedules_api(client):
    names = {x["name"] for x in client.get("/api/schedules").json()}
    assert {"prune", "library_refresh", "library_check"} <= names
    r = client.put("/api/schedules/prune", json={"cron": "15 2 * * *"})
    assert next(x for x in r.json() if x["name"] == "prune")["when"] == "daily at 02:15"
    assert client.put("/api/schedules/prune", json={"cron": "nope"}).status_code == 400
    assert client.post("/api/schedules/missing/run").status_code == 404
    assert client.put("/api/settings", json={"schedule": {"prune": "bad"}}).status_code == 400


def test_prune_task(client):
    from tests.test_api import core_of

    out = core_of(client).prune()
    assert {"art_removed", "jobs", "http_cache"} <= set(out)

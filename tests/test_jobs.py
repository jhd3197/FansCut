import threading
import time

import pytest

from fanscut.errors import JobCancelled, NotFoundError
from fanscut.jobs import JobStore, Worker


def make(handlers):
    from fanscut.db import Database

    store = JobStore(Database(":memory:"))
    return store, Worker(store, handlers)


def test_job_runs_to_done_with_result_and_progress():
    def handler(payload, ctx):
        ctx.progress(0.5, "halfway")
        return {"echo": payload["x"], "message": "finished"}

    store, worker = make({"echo": handler})
    job = worker.submit("echo", "Echo", {"x": 3})
    assert job["status"] == "queued"
    assert worker.run_pending() == 1
    done = store.get(job["id"])
    assert (done["status"], done["progress"], done["message"]) == ("done", 1.0, "finished")
    assert done["result"]["echo"] == 3


def test_failure_is_recorded_not_raised():
    def boom(_p, _c):
        raise RuntimeError("disk on fire")

    store, worker = make({"boom": boom})
    jid = worker.submit("boom", "Boom", {})["id"]
    worker.run_pending()
    j = store.get(jid)
    assert j["status"] == "failed" and "disk on fire" in j["message"]


def test_cancel_queued_job_never_runs():
    ran = []
    store, worker = make({"k": lambda p, c: ran.append(1) or {}})
    jid = worker.submit("k", "t", {})["id"]
    assert worker.cancel(jid)["status"] == "cancelled"
    worker.run_pending()
    assert ran == []


def test_cancel_running_job_via_event():
    started = threading.Event()

    def slow(_p, ctx):
        started.set()
        while not ctx.cancel.is_set():
            time.sleep(0.01)
        raise JobCancelled("stop")

    store, worker = make({"slow": slow})
    jid = worker.submit("slow", "t", {})["id"]
    t = threading.Thread(target=worker.run_pending)
    t.start()
    assert started.wait(2)
    assert worker.cancel(jid)["status"] == "running"
    t.join(2)
    assert store.get(jid)["status"] == "cancelled"


def test_background_thread_processes_jobs_and_stops():
    store, worker = make({"k": lambda p, c: {"ok": True}})
    worker.start()
    jid = worker.submit("k", "t", {})["id"]
    for _ in range(100):
        if store.get(jid)["status"] == "done":
            break
        time.sleep(0.05)
    worker.stop()
    assert store.get(jid)["status"] == "done"


def test_running_jobs_requeued_on_start():
    store, worker = make({"k": lambda p, c: {}})
    jid = store.add("k", "t", {})["id"]
    store.update(jid, status="running")
    assert store.requeue_running() == 1
    assert store.get(jid)["status"] == "queued"


def test_unknown_kind_and_missing_job():
    store, worker = make({})
    with pytest.raises(ValueError, match="unknown job kind"):
        worker.submit("nope", "t", {})
    with pytest.raises(NotFoundError):
        store.get("missing")


def test_delete_rules_and_listing_order():
    store, worker = make({"k": lambda p, c: {}})
    first = worker.submit("k", "first", {})["id"]
    second = worker.submit("k", "second", {})["id"]
    assert [j["title"] for j in store.list()] == ["second", "first"]
    with pytest.raises(ValueError, match="cancel"):
        store.delete(first)
    worker.run_pending()
    store.delete(first)
    assert [j["id"] for j in store.list()] == [second]

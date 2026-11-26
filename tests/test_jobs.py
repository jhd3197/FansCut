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


def _thread_stacks() -> str:
    """Every live thread's stack, for a failure that only shows up on CI."""
    import sys
    import traceback

    names = {t.ident: t.name for t in threading.enumerate()}
    return "\n".join(f"--- {names.get(tid, tid)}\n{''.join(traceback.format_stack(frame))}"
                     for tid, frame in sys._current_frames().items())


def test_background_thread_processes_jobs_and_stops():
    ran = threading.Event()

    def handler(_p, _c):
        ran.set()
        return {"ok": True}

    store, worker = make({"k": handler})
    worker.start()
    jid = worker.submit("k", "t", {})["id"]
    # This failed on CI a few times with the job still queued after 20 s and
    # nothing logged; if it happens again, say what the worker was doing.
    if not ran.wait(30):
        alive = worker.alive()
        pytest.fail(f"job never started (status {store.get(jid)['status']}, worker alive: "
                    f"{alive})\n{_thread_stacks()}")
    for _ in range(100):  # the handler returned; the store update follows
        if store.get(jid)["status"] == "done":
            break
        time.sleep(0.05)
    worker.stop()
    assert store.get(jid)["status"] == "done"
    assert worker._threads and not any(t.is_alive() for t in worker._threads)


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


def test_duplicate_submits_return_the_queued_job():
    store, worker = make({"k": lambda p, c: {}})
    a = worker.submit("k", "Render Harbor Lights", {"seasons": [1]})
    b = worker.submit("k", "Render Harbor Lights", {"seasons": [1]})
    c = worker.submit("k", "Render Harbor Lights", {"seasons": [2]})
    assert a["id"] == b["id"] and c["id"] != a["id"]
    assert len(store.list()) == 2


def test_after_waits_for_its_dependency_across_lanes():
    order = []
    store, worker = make({"detect": lambda p, c: order.append("detect") or {},
                          "render": lambda p, c: order.append("render") or {}})
    det = worker.submit("detect", "d", {})
    worker.submit("render", "r", {"after": [det["id"]]})
    assert store.get(det["id"])["lane"] == "light"
    assert worker.run_pending("heavy") == 0  # the render waits for detection
    worker.run_pending("light")
    worker.run_pending("heavy")
    assert order == ["detect", "render"]


def test_cancel_check_and_parts():
    seen = []

    def handler(_p, ctx):
        prog = ctx.part(1, 4)
        prog(0.5)
        seen.append(store.get(ctx.job_id)["progress"])
        ctx.cancel.set()
        ctx.check()
        return {}

    store, worker = make({"k": handler})
    jid = worker.submit("k", "t", {})["id"]
    worker.run_pending()
    assert seen == [0.375] and store.get(jid)["status"] == "cancelled"


def test_on_finish_called_with_the_finished_job():
    got = []
    from fanscut.db import Database

    store = JobStore(Database(":memory:"))
    worker = Worker(store, {"k": lambda p, c: {"message": "ok"}}, on_finish=got.append)
    worker.submit("k", "t", {})
    worker.run_pending()
    assert got[0]["status"] == "done"

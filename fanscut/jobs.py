"""Job queue in SQLite plus two worker threads.

A job is {kind, payload}; handlers are registered per kind and return a result
dict. Jobs left "running" by a previous process are re-queued at startup.

Two lanes, so a long render does not hold up quick work: "heavy" (renders)
and "light" (detection, syncs, lookups), one job at a time in each. A job
whose payload names another job in "after" waits until that one has finished
(the webhook's render waits for its detection). Submitting a job identical
to one still queued returns the queued one instead of adding a twin.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .db import Database, dumps
from .errors import JobCancelled, NotFoundError

log = logging.getLogger("fanscut.jobs")

ACTIVE = ("queued", "running")
HEAVY = frozenset({"render", "timeline_render"})
LANES = ("heavy", "light")


def lane_of(kind: str) -> str:
    return "heavy" if kind in HEAVY else "light"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class JobContext:
    store: JobStore
    job_id: str
    cancel: threading.Event

    def progress(self, p: float, message: str | None = None) -> None:
        self.check()
        extra = {"message": message} if message else {}
        self.store.update(self.job_id, progress=round(p, 4), **extra)

    def check(self) -> None:
        """Raise JobCancelled if the job was cancelled; call between units of work."""
        if self.cancel.is_set():
            raise JobCancelled("cancelled")

    def part(self, i: int, n: int) -> Callable[[float], None]:
        """A progress callback for step i of n, mapped onto the job's whole bar."""
        return lambda p: self.progress((i + max(0.0, min(1.0, p))) / max(1, n))


Handler = Callable[[dict[str, Any], JobContext], dict[str, Any]]


class JobStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["result"] = json.loads(d["result"])
        return d

    def add(self, kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        jid = uuid.uuid4().hex[:12]
        now = _now()
        self.db.execute(
            "INSERT INTO jobs (id, kind, title, payload, lane, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (jid, kind, title, dumps(payload), lane_of(kind), now, now),
        )
        return self.get(jid)

    def find_queued(self, kind: str, title: str, payload: dict[str, Any]
                    ) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM jobs WHERE status = 'queued' AND kind = ? AND title = ?"
                          " AND payload = ? ORDER BY created_at LIMIT 1",
                          (kind, title, dumps(payload)))
        return self._row(row) if row else None

    def get(self, jid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM jobs WHERE id = ?", (jid,))
        if row is None:
            raise NotFoundError(f"job {jid} not found")
        return self._row(row)

    def list(self, limit: int = 100, skip: int = 0, status: str | None = None
             ) -> list[dict[str, Any]]:
        where, args = ("WHERE status = ?", [status]) if status else ("", [])
        rows = self.db.query(f"SELECT * FROM jobs {where} ORDER BY created_at DESC, rowid DESC"  # noqa: S608
                             " LIMIT ? OFFSET ?", (*args, limit, skip))
        return [self._row(r) for r in rows]

    def count(self, status: str | None = None) -> int:
        row = (self.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status = ?", (status,))
               if status else self.db.one("SELECT COUNT(*) AS n FROM jobs"))
        return int(row["n"]) if row else 0

    def depth(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.db.query(
            "SELECT status, COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
            " GROUP BY status")}

    def update(self, jid: str, **fields: Any) -> None:
        for k in ("payload", "result"):
            if k in fields:
                fields[k] = dumps(fields[k])
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), jid))  # noqa: S608

    def next_queued(self, lane: str | None = None) -> dict[str, Any] | None:
        """The oldest queued job (in `lane`) whose "after" job, if any, has finished."""
        where, args = ("AND lane = ?", (lane,)) if lane else ("", ())
        for row in self.db.query(f"SELECT * FROM jobs WHERE status = 'queued' {where}"  # noqa: S608
                                 " ORDER BY created_at, rowid", args):
            job = self._row(row)
            after = job["payload"].get("after") or []
            waiting = [a for a in ([after] if isinstance(after, str) else after)
                       if (dep := self.db.one("SELECT status FROM jobs WHERE id = ?", (a,)))
                       is not None and dep["status"] in ACTIVE]
            if not waiting:
                return job
        return None

    def requeue_running(self) -> int:
        n = len(self.db.query("SELECT id FROM jobs WHERE status = 'running'"))
        self.db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
        return n

    def delete(self, jid: str) -> None:
        if self.get(jid)["status"] in ACTIVE:
            raise ValueError("cancel the job before deleting it")
        self.db.execute("DELETE FROM jobs WHERE id = ?", (jid,))


Finished = Callable[[dict[str, Any]], None]


class Worker:
    def __init__(self, store: JobStore, handlers: dict[str, Handler],
                 on_finish: Finished | None = None):
        self.store = store
        self.handlers = handlers
        self.on_finish = on_finish  # called with the job once it is done or failed
        self._wakes = {lane: threading.Event() for lane in LANES}
        self._stop = threading.Event()
        self._cancels: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.store.requeue_running()
        self._threads = [threading.Thread(target=self._loop, args=(lane,),
                                          name=f"fanscut-worker-{lane}", daemon=True)
                         for lane in LANES]
        for t in self._threads:
            t.start()

    def alive(self) -> bool:
        """True when every lane's thread is running (false before start())."""
        return bool(self._threads) and all(t.is_alive() for t in self._threads)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for ev in list(self._cancels.values()):
            ev.set()
        for w in self._wakes.values():
            w.set()
        for t in self._threads:
            t.join(timeout)

    def submit(self, kind: str, title: str, payload: dict[str, Any],
               dedupe: bool = True) -> dict[str, Any]:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind {kind!r}")
        if dedupe and (twin := self.store.find_queued(kind, title, payload)) is not None:
            return twin
        job = self.store.add(kind, title, payload)
        for w in self._wakes.values():  # a light job may unblock a heavy one, and back
            w.set()
        return job

    def cancel(self, jid: str) -> dict[str, Any]:
        job = self.store.get(jid)
        if job["status"] == "queued":
            self.store.update(jid, status="cancelled", message="cancelled before it started")
        elif job["status"] == "running" and jid in self._cancels:
            self._cancels[jid].set()
        return self.store.get(jid)

    def run_pending(self, lane: str | None = None) -> int:
        """Run every queued job (of `lane`) on this thread: the loop's body, and what
        tests call directly. With no lane, jobs run in queue order across both."""
        n = 0
        while (job := self.store.next_queued(lane)) is not None:
            self._run(job)
            n += 1
        return n

    def _loop(self, lane: str) -> None:
        wake = self._wakes[lane]
        while not self._stop.is_set():
            try:
                if not self.run_pending(lane):
                    wake.wait(timeout=2.0)
                    wake.clear()
            except Exception:  # noqa: BLE001 - the worker must outlive any one bad job
                log.exception("worker loop error")
                self._stop.wait(2.0)

    def _run(self, job: dict[str, Any]) -> None:
        jid = job["id"]
        cancel = self._cancels[jid] = threading.Event()
        self.store.update(jid, status="running", progress=0)
        try:
            result = self.handlers[job["kind"]](job["payload"], JobContext(self.store, jid, cancel))
        except JobCancelled:
            self.store.update(jid, status="cancelled", message="cancelled")
        except Exception as exc:  # noqa: BLE001 - recorded on the job, not raised
            log.exception("job %s failed", jid)
            self.store.update(jid, status="failed", message=str(exc) or exc.__class__.__name__)
        else:
            self.store.update(jid, status="done", progress=1.0, result=result,
                              message=result.get("message", "done"))
        finally:
            self._cancels.pop(jid, None)
            for w in self._wakes.values():  # jobs waiting on this one may go now
                w.set()
        if self.on_finish is not None:
            try:
                self.on_finish(self.store.get(jid))
            except Exception:  # noqa: BLE001 - a notification must not break the queue
                log.exception("on_finish for job %s failed", jid)

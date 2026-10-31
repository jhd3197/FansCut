"""Job queue in SQLite plus one worker thread.

A job is {kind, payload}; handlers are registered per kind and return a result
dict. Jobs left "running" by a previous process are re-queued at startup.
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


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


@dataclass
class JobContext:
    store: JobStore
    job_id: str
    cancel: threading.Event

    def progress(self, p: float, message: str | None = None) -> None:
        extra = {"message": message} if message else {}
        self.store.update(self.job_id, progress=round(p, 4), **extra)


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
            "INSERT INTO jobs (id, kind, title, payload, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (jid, kind, title, dumps(payload), now, now),
        )
        return self.get(jid)

    def get(self, jid: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM jobs WHERE id = ?", (jid,))
        if row is None:
            raise NotFoundError(f"job {jid} not found")
        return self._row(row)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                             (limit,))
        return [self._row(r) for r in rows]

    def update(self, jid: str, **fields: Any) -> None:
        for k in ("payload", "result"):
            if k in fields:
                fields[k] = dumps(fields[k])
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), jid))  # noqa: S608

    def next_queued(self) -> dict[str, Any] | None:
        row = self.db.one("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at, rowid")
        return self._row(row) if row else None

    def requeue_running(self) -> int:
        n = len(self.db.query("SELECT id FROM jobs WHERE status = 'running'"))
        self.db.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'")
        return n

    def delete(self, jid: str) -> None:
        if self.get(jid)["status"] in ACTIVE:
            raise ValueError("cancel the job before deleting it")
        self.db.execute("DELETE FROM jobs WHERE id = ?", (jid,))


class Worker:
    def __init__(self, store: JobStore, handlers: dict[str, Handler]):
        self.store = store
        self.handlers = handlers
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancels: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.store.requeue_running()
        self._thread = threading.Thread(target=self._loop, name="fanscut-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for ev in self._cancels.values():
            ev.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def submit(self, kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind {kind!r}")
        job = self.store.add(kind, title, payload)
        self._wake.set()
        return job

    def cancel(self, jid: str) -> dict[str, Any]:
        job = self.store.get(jid)
        if job["status"] == "queued":
            self.store.update(jid, status="cancelled", message="cancelled before it started")
        elif job["status"] == "running" and jid in self._cancels:
            self._cancels[jid].set()
        return self.store.get(jid)

    def run_pending(self) -> int:
        """Run every queued job on this thread (the loop's body; tests call it directly)."""
        n = 0
        while (job := self.store.next_queued()) is not None:
            self._run(job)
            n += 1
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.run_pending():
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
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

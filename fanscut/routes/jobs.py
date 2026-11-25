from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from .. import config
from ..errors import FansCutError, NotFoundError
from ..scheduler import Cron, CronError
from . import CoreDep

router = APIRouter(prefix="/api", tags=["jobs"])

Status = Literal["queued", "running", "done", "failed", "cancelled"]


class Job(BaseModel):
    id: str
    kind: str
    title: str
    status: Status
    progress: float
    message: str
    payload: dict[str, Any]
    result: dict[str, Any]
    lane: str = "light"
    created_at: str
    updated_at: str


class PageInfo(BaseModel):
    skip: int
    take: int
    results: int  # total matching, across pages
    pages: int


class JobList(BaseModel):
    jobs: list[Job]
    page_info: PageInfo


@router.get("/jobs", response_model=JobList)
def list_jobs(core: CoreDep, status: Status | None = None, skip: int = Query(0, ge=0),
              take: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    """Newest first. `status` filters; `skip`/`take` page through the rest."""
    total = core.jobs.count(status)
    return {"jobs": core.jobs.list(take, skip, status),
            "page_info": {"skip": skip, "take": take, "results": total,
                          "pages": -(-total // take)}}


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(core: CoreDep, job_id: str) -> dict[str, Any]:
    return core.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel", response_model=Job)
def cancel_job(core: CoreDep, job_id: str) -> dict[str, Any]:
    return core.worker.cancel(job_id)


@router.delete("/jobs/{job_id}")
def delete_job(core: CoreDep, job_id: str) -> dict[str, bool]:
    core.jobs.delete(job_id)
    return {"ok": True}


# -- scheduled tasks ------------------------------------------------------------------
class Schedule(BaseModel):
    name: str
    description: str
    cron: str
    when: str
    next_run: str | None
    running: bool
    last_run: str | None
    last_result: dict[str, Any] | None
    last_error: str | None


class ScheduleBody(BaseModel):
    cron: str  # 5-field cron; "" turns the task off


def _task(core: Any, name: str) -> None:
    if name not in core.scheduler.tasks:
        raise NotFoundError(f"no scheduled task {name!r}")


@router.get("/schedules", response_model=list[Schedule], tags=["schedules"])
def list_schedules(core: CoreDep) -> list[dict[str, Any]]:
    return core.scheduler.describe()


@router.post("/schedules/{name}/run", tags=["schedules"])
def run_schedule(core: CoreDep, name: str) -> dict[str, Any]:
    _task(core, name)
    return {"started": core.scheduler.run(name)}


@router.post("/schedules/{name}/cancel", tags=["schedules"])
def cancel_schedule(core: CoreDep, name: str) -> dict[str, Any]:
    _task(core, name)
    return {"cancelling": core.scheduler.cancel(name)}


@router.put("/schedules/{name}", response_model=list[Schedule], tags=["schedules"])
def set_schedule(core: CoreDep, name: str, body: ScheduleBody,
                 request: Request) -> list[dict[str, Any]]:
    """Change a task's cron and save it to config.yaml."""
    _task(core, name)
    cron = body.cron.strip()
    if cron:
        try:
            Cron.parse(cron)
        except CronError as exc:
            raise FansCutError(str(exc)) from exc
    new = config.merge_update(core.cfg, {"schedule": {name: cron}})
    path: Path = request.app.state.config_path
    try:
        config.save(new, path)
    except OSError as exc:
        raise FansCutError(f"could not write {path}: {exc.strerror}") from exc
    core.apply_config(new)
    return core.scheduler.describe()

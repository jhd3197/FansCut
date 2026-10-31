from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import CoreDep

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs")
def list_jobs(core: CoreDep) -> dict[str, Any]:
    return {"jobs": core.jobs.list()}


@router.get("/jobs/{job_id}")
def get_job(core: CoreDep, job_id: str) -> dict[str, Any]:
    return core.jobs.get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(core: CoreDep, job_id: str) -> dict[str, Any]:
    return core.worker.cancel(job_id)


@router.delete("/jobs/{job_id}")
def delete_job(core: CoreDep, job_id: str) -> dict[str, bool]:
    core.jobs.delete(job_id)
    return {"ok": True}

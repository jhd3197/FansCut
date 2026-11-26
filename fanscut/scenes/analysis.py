"""A film's analysis as four steps (shots, faces, speech, tags), for the film page.

Each step reads what is stored (the index, the measured signals, the tags) and the
newest scene job for the film, so a running, queued or failed job shows on its step.
``start`` queues whatever is missing, chaining on the index job with ``after``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..errors import FansCutError
from . import vocab

if TYPE_CHECKING:
    from ..core import Core

# A film counts as tagged once a whole-film labelling pass finished, or once it has
# this many tagged moments (labelling done before jobs were kept, or by hand).
TAGGED_MOMENTS = 5

KINDS = ("scene_index", "scene_signals", "scene_label")
ACTIVE = ("queued", "running")


def _jobs(core: Core, key: str) -> dict[str, dict[str, Any]]:
    """The newest job of each scene kind for this film, and whether a full label
    pass ever finished ("label_done")."""
    out: dict[str, dict[str, Any]] = {}
    rows = core.db.query(
        "SELECT id, kind, status, progress, message, payload FROM jobs"
        " WHERE kind IN (?,?,?) ORDER BY created_at DESC, rowid DESC LIMIT 500",
        KINDS,
    )
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        if payload.get("key") != key:
            continue
        job = {
            "id": r["id"],
            "status": r["status"],
            "progress": r["progress"],
            "message": r["message"],
        }
        out.setdefault(r["kind"], job)
        if (
            r["kind"] == "scene_label"
            and r["status"] == "done"
            and payload.get("mode", "full") == "full"
        ):
            out.setdefault("label_done", job)
    return out


def _moments(core: Core, key: str) -> int:
    """Tagged moments with at least one content tag (cut tags and fc: ones don't count)."""
    content = {t["id"] for t in vocab.load()["tags"]}
    n = 0
    for r in core.scenes.store.tags([key]):
        if r["status"] == "rejected":
            continue
        if any(t in content or not (t.startswith("fc:") or vocab.get(t)) for t in r["tags"]):
            n += 1
    return n


def _step(sid: str, state: str, detail: str, job: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": sid, "state": state, "detail": detail, "job": job}


def _with_job(step: dict[str, Any], job: dict[str, Any] | None) -> dict[str, Any]:
    """A queued or running job wins over the stored state; a failed one shows unless
    the step got done since."""
    if not job:
        return step
    if job["status"] in ACTIVE:
        return {**step, "state": job["status"], "job": job}
    if job["status"] == "failed" and step["state"] != "done":
        return {**step, "state": "failed", "detail": job["message"] or "Failed", "job": job}
    return step


def status(core: Core, key: str) -> dict[str, Any]:
    m = core.scenes.film(key)
    doc = core.scenes.store.index(key)
    signals = core.scenes.store.signals(key)
    jobs = _jobs(core, key)
    moments = _moments(core, key)
    ai = bool(core.cfg.ai.model)
    index_job = jobs.get("scene_index")

    if doc:
        n_shots, n_scenes = len(doc.get("shots") or []), len(doc.get("scenes") or [])
        shots = _step("shots", "done", f"{n_shots} shots, {n_scenes} scenes")
        lines = int((doc.get("subtitles") or {}).get("lines") or len(doc.get("lines") or []))
        speech = (
            _step("speech", "done", f"{lines} subtitle lines")
            if lines
            else _step("speech", "todo", "No subtitles: transcribe the audio")
        )
    else:
        shots = _step("shots", "todo", "Not indexed")
        speech = _step("speech", "todo", "Read with the shots")
    shots, speech = _with_job(shots, index_job), _with_job(speech, index_job)

    if signals is None:
        faces = _step("faces", "todo", "Not measured" if doc else "Needs the shots first")
    elif signals.get("faces"):
        faces = _step("faces", "done", "Measured")
    else:
        faces = _step("faces", "off", "Measured without a face model")
    faces = _with_job(faces, jobs.get("scene_signals"))

    tagged = bool(jobs.get("label_done")) or moments >= TAGGED_MOMENTS
    if tagged:
        tags = _step("tags", "done", f"{moments} moments")
    elif not ai:
        tags = _step("tags", "off", "Set an AI model in Settings")
    elif moments:
        tags = _step("tags", "todo", f"{moments} moment{'s' if moments != 1 else ''} so far")
    else:
        tags = _step("tags", "todo", "Not tagged" if doc else "Needs the shots first")
    tags = _with_job(tags, jobs.get("scene_label"))

    return {
        "key": key,
        "title": m.title,
        "on_disk": bool(m.file),
        "analysed": bool(doc) and tagged,
        "ai": ai,
        "steps": [shots, faces, speech, tags],
    }


def start(core: Core, key: str) -> dict[str, Any]:
    """Queue every missing step: the index, then signals and labelling after it."""
    m, _ = core.scenes._file(key)
    now = status(core, key)
    by = {s["id"]: s for s in now["steps"]}
    busy = lambda s: s["state"] in ACTIVE  # noqa: E731
    queued: list[dict[str, Any]] = []

    after: list[str] = []
    if by["shots"]["state"] in ("todo", "failed"):
        job = core.scenes.submit_index(key)
        queued.append(job)
        after = [job["id"]]
    elif busy(by["shots"]):
        after = [by["shots"]["job"]["id"]]

    # a new index queues the signals itself when Settings measure them after indexing
    auto_signals = bool(after) and core.cfg.scenes.signals and by["shots"]["state"] != "done"
    if by["faces"]["state"] in ("todo", "failed") and not auto_signals:
        queued.append(
            core.worker.submit(
                "scene_signals", f"Measure signals of {m.title}", {"key": key, "after": after}
            )
            if after
            else core.scenes.submit_signals(key)
        )

    if by["tags"]["state"] in ("todo", "failed"):
        if after:
            off = set(core.cfg.scenes.families_off)
            families = [f for f in vocab.families() if f not in off]
            if not families:
                raise FansCutError("pick at least one tag family in Settings")
            queued.append(
                core.worker.submit(
                    "scene_label",
                    f"Tag {m.title}",
                    {"key": key, "families": families, "sweep": False, "mode": "full",
                     "after": after},
                )
            )
        else:
            queued.append(core.scenes.submit_label(key))
    return {"queued": queued, **status(core, key)}

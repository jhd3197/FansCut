"""A film's analysis as four steps (shots, faces, speech, tags), for the film page.

Marcarr runs the analysis (link.py) in seven steps; the film page shows four:
shots = Marcarr's shots and scenes, faces = its measured signals and face matches,
speech = its subtitles, tags = its tags step. ``start`` queues what is missing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .link import title_id

if TYPE_CHECKING:
    from ..core import Core

# Marcarr's step state -> the film page's
STATE = {
    "done": "done",
    "rerun": "todo",
    "failed": "failed",
    "run": "running",
    "queued": "queued",
    "notrun": "todo",
    "none": "off",
    "off": "off",
}
ORDER = ("failed", "running", "queued", "todo", "off", "done")  # a merged step shows the first


def _step(sid: str, state: str, detail: str) -> dict[str, Any]:
    return {"id": sid, "state": state, "detail": detail, "job": None}


def _merge(sid: str, parts: list[tuple[str, str]]) -> dict[str, Any]:
    """Several Marcarr steps as one: the state that most needs attention, its detail."""
    state, detail = min(parts, key=lambda p: ORDER.index(p[0]))
    return _step(sid, state, detail)


def status(core: Core, key: str) -> dict[str, Any]:
    film = core.scenes.film(key)
    ai = bool(core.cfg.ai.model)
    vid = core.scenes.link.video(film)
    m = core.scenes.marcarr
    if vid is None:
        info: dict[str, dict[str, Any]] = {}
    else:
        from marcarr.views import Views

        info = Views(m).info(vid)

    def get(step: str, todo: str) -> tuple[str, str]:
        i = info.get(step)
        if not i:
            return "todo", todo
        state = STATE.get(i["state"], "todo")
        return state, i.get("detail") or ("Done" if state == "done" else todo)

    shots = _merge("shots", [get("shots", "Not indexed"), get("scenes", "Not indexed")])
    if shots["state"] == "done":
        n = len((m.store.result(vid, "scenes") or {}).get("scenes") or []) if vid else 0
        if n:
            shots["detail"] = f"{shots['detail']}, {n} scene{'s' if n != 1 else ''}"
    speech_state, speech_detail = get("subtitles", "Read with the shots")
    if speech_state == "off":
        speech_state, speech_detail = "todo", "No subtitles: transcribe the audio"
    faces = _merge("faces", [get("signals", "Not measured"), get("faces", "Not looked at")])
    if not ai:
        tags = _step("tags", "off", "Set an AI model in Settings")
    else:
        tags = _step("tags", *get("tags", "Not tagged"))
    steps = [shots, faces, _step("speech", speech_state, speech_detail), tags]
    return {
        "key": key,
        "title": film.title,
        "on_disk": bool(film.file),
        "analysed": shots["state"] == "done" and tags["state"] == "done",
        "ai": ai,
        "video": vid,
        "title_id": title_id(film),
        "steps": steps,
    }


def start(core: Core, key: str) -> dict[str, Any]:
    """Queue every step Marcarr has left to do for this film."""
    film, _ = core.scenes._file(key)
    vid = core.scenes.link.video(film, register=True)
    out = core.scenes.marcarr.pipeline.submit(vid, only_needed=True)
    queued = []
    if out["queued"]:
        queued.append(
            {
                "id": out.get("job"),
                "kind": "marcarr",
                "title": f"Analyse {film.title}",
                "steps": out.get("steps") or [],
                "status": "queued",
            }
        )
    return {"queued": queued, **status(core, key)}

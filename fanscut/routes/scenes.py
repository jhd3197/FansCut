"""Scene tagging: film index, frames and contact sheets, tags, cast, X-Ray, AI jobs.

Film keys contain ':' and sometimes spaces ("foldermovie:Inception (2010)"),
so they travel as a ``key`` query parameter.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import CoreDep

router = APIRouter(prefix="/api/scenes", tags=["scenes"])


@router.get("/films")
def films(core: CoreDep, collection: str | None = None) -> dict[str, Any]:
    """Movies with their index and tag status (optionally one collection)."""
    return {"films": core.scenes.films(collection)}


@router.get("/film")
def film(core: CoreDep, key: str, lines: bool = True) -> dict[str, Any]:
    """Index (shots, subtitle lines, automatic scenes), tags and cast of one film."""
    return core.scenes.overview(key, with_lines=lines)


class IndexBody(BaseModel):
    key: str
    transcribe: bool = False  # no text subtitles: transcribe the audio (needs an STT key)


@router.post("/index", status_code=202)
def index_film(core: CoreDep, body: IndexBody) -> dict[str, Any]:
    return core.scenes.submit_index(body.key, body.transcribe)


class KeyBody(BaseModel):
    key: str


@router.post("/signals", status_code=202)
def measure_signals(core: CoreDep, body: KeyBody) -> dict[str, Any]:
    """Measure the film's signals (faces, shots, brightness, sound): no AI."""
    return core.scenes.submit_signals(body.key)


@router.get("/signals")
def get_signals(core: CoreDep, key: str) -> dict[str, Any]:
    """Measured ranges per signal: internal (posters, extending cuts), never tags."""
    return core.scenes.signals(key)


@router.get("/transcript")
def transcript(
    core: CoreDep,
    key: str,
    start: float = Query(0, ge=0),
    end: float | None = Query(None, gt=0),
    speech: bool = False,
) -> dict[str, Any]:
    """Subtitle lines; ``speech=true`` keeps only what is said (the join check uses it)."""
    return {"lines": core.scenes.transcript(key, start, end, speech)}


@router.get("/shots")
def shots(
    core: CoreDep, key: str, start: float = Query(0, ge=0), end: float | None = Query(None, gt=0)
) -> dict[str, Any]:
    return {"shots": core.scenes.shots(key, start, end)}


@router.get("/sheet")
def sheet(
    core: CoreDep,
    key: str,
    start: float = Query(ge=0),
    end: float = Query(gt=0),
    cols: int = Query(3, ge=1, le=6),
    rows: int = Query(2, ge=1, le=6),
    height: int = Query(240, ge=90, le=720),
) -> FileResponse:
    """A contact sheet: frames of the range in a labelled grid (one JPEG)."""
    path, times = core.scenes.sheet(key, start, end, cols, rows, height)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "X-Tile-Times": ",".join(f"{t:.2f}" for t in times),
            "Cache-Control": "max-age=86400",
        },
    )


@router.get("/frame")
def frame(
    core: CoreDep, key: str, t: float = Query(ge=0), height: int = Query(240, ge=90, le=1080)
) -> FileResponse:
    return FileResponse(
        core.scenes.frame(key, t, height),
        media_type="image/jpeg",
        headers={"Cache-Control": "max-age=86400"},
    )


@router.get("/clip")
def clip(
    core: CoreDep, key: str, start: float = Query(ge=0), end: float = Query(gt=0)
) -> FileResponse:
    """A playable 480p MP4 of the range (5 minutes at most)."""
    return FileResponse(core.scenes.clip(key, start, end), media_type="video/mp4")


@router.get("/cast")
def cast(core: CoreDep, key: str, refresh: bool = False) -> dict[str, Any]:
    return {"cast": core.scenes.get_cast(key, refresh)}


@router.get("/faces")
def faces(core: CoreDep, key: str, t: float = Query(ge=0)) -> dict[str, Any]:
    """Faces in the frame at t: boxes (fractions of the frame), named against the
    cast photos; `sure` is false for names a person should confirm."""
    return core.scenes.faces_at(key, t)


@router.get("/xray")
def xray(core: CoreDep, key: str, t: float = Query(ge=0)) -> dict[str, Any]:
    """Who is on screen at t, from the tags covering it, and their other scenes."""
    return core.scenes.xray(key, t)


class TagBody(BaseModel):
    key: str
    start: float
    end: float
    tags: list[str]
    people: list[Any] = Field(default_factory=list)
    summary: str = ""
    status: str | None = None
    confidence: float | None = None
    source: str | None = None
    snap: bool = True


@router.post("/tags", status_code=201)
def add_tag(core: CoreDep, body: TagBody) -> dict[str, Any]:
    """Tag a range. A person's tag is accepted; set status=proposed for a suggestion."""
    data = body.model_dump(exclude_none=True)
    return core.scenes.add_tag(body.key, data, source=body.source or "user")


@router.get("/tags")
def list_tags(
    core: CoreDep, key: str | None = None, tag: str | None = None, status: str | None = None
) -> dict[str, Any]:
    return {"tags": core.scenes.tags([key] if key else None, tag, status)}


@router.get("/vocab")
def vocab_list() -> dict[str, Any]:
    """The shared tag list: content tags by family, FansCut's cut tags and the signals."""
    from ..scenes import vocab

    return vocab.public()


@router.get("/tag-counts")
def tag_counts(core: CoreDep) -> dict[str, Any]:
    return {"tags": core.scenes.tag_counts()}


@router.patch("/tags/{tid}")
def update_tag(core: CoreDep, tid: str, body: dict[str, Any]) -> dict[str, Any]:
    return core.scenes.update_tag(tid, body)


@router.delete("/tags/{tid}", status_code=204)
def delete_tag(core: CoreDep, tid: str) -> Response:
    core.scenes.delete_tag(tid)
    return Response(status_code=204)


class AIBody(BaseModel):
    key: str
    tag: str
    description: str = ""
    verify: bool = True  # check every candidate on a contact sheet
    sweep: bool = False  # also look at scenes with no dialogue


@router.post("/ai", status_code=202)
def ai_tag(core: CoreDep, body: AIBody) -> dict[str, Any]:
    return core.scenes.submit_ai(body.key, body.tag, body.description, body.verify, body.sweep)


@router.post("/tidy")
def tidy(core: CoreDep, body: KeyBody) -> dict[str, Any]:
    """Accept AI moments at or above Settings' threshold and merge duplicate moments."""
    return core.scenes.tidy(body.key)


class LabelBody(BaseModel):
    key: str
    families: list[str] | None = None  # default: every family not switched off in Settings
    sweep: bool = False  # also look at stretches with no dialogue
    mode: str = "full"  # "focused": only the close search of occasions (Settings, cues)


@router.post("/label", status_code=202)
def label(core: CoreDep, body: LabelBody) -> dict[str, Any]:
    """Tag a film with the whole tag list in one pass (proposals)."""
    return core.scenes.submit_label(body.key, body.families, body.sweep, body.mode)


@router.get("/collection")
def collection(
    core: CoreDep, name: str, tag: str | None = None, status: str | None = None
) -> dict[str, Any]:
    return core.scenes.collection(name, tag, status)


class TimelineBody(BaseModel):
    collection: str  # names the cut; picks the films unless `films` is given
    tag: str
    title: str = ""
    films: list[str] | None = None  # film keys, for a franchise Plex doesn't group


@router.post("/to-timeline", status_code=201)
def to_timeline(core: CoreDep, body: TimelineBody) -> dict[str, Any]:
    """Accepted scenes with a tag across a collection, as a timeline cut."""
    return core.scenes.to_timeline(body.collection, body.tag, body.title, body.films)


@router.get("/analysis")
def analysis(core: CoreDep, key: str) -> dict[str, Any]:
    """The film's analysis steps (shots, faces, speech, tags), each with its job."""
    from ..scenes import analysis as an

    return an.status(core, key)


@router.post("/analyse", status_code=202)
def analyse(core: CoreDep, body: KeyBody) -> dict[str, Any]:
    """Queue every missing analysis step: index, then signals and tags after it."""
    from ..scenes import analysis as an

    return an.start(core, body.key)

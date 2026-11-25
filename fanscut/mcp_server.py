"""An MCP server that lets an AI client (Claude Code, Claude Desktop, ...) tag films.

It talks to the running FansCut web app over its REST API, so tags, jobs and
caches stay in one place, and it can only do what the API allows. The tools are
plain functions turned into Prompture tool definitions and served with
``prompture.mcp``.

Run it with ``fanscut mcp`` (stdio). Point it at another FansCut with
``--url`` or ``FANSCUT_URL``. Typical session for "Christmas at Hogwarts":

1. ``list_films(collection="Harry Potter")`` and ``index_film`` where needed.
2. ``read_dialogue`` in chunks; note the stretches that sound like Christmas.
3. ``look`` at each stretch (a labelled frame grid) to confirm and tighten it.
4. ``propose_scene`` for each hit; a person accepts them in the tagger.
5. ``make_timeline(collection, "christmas")`` once they are accepted.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:6464"
MAX_TEXT = 20_000
_client: httpx.Client | None = None


def base_url() -> str:
    return os.environ.get("FANSCUT_URL", DEFAULT_URL).rstrip("/")


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=base_url(), timeout=600)
    return _client


def use_client(client: httpx.Client) -> None:
    """Send requests through *client* (tests, or a custom transport)."""
    global _client
    _client = client


def _call(method: str, path: str, **kw: Any) -> Any:
    r = _http().request(method, path, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text[:300]
        raise RuntimeError(f"FansCut said {r.status_code}: {detail}")
    if r.status_code == 204 or not r.content:
        return None
    ctype = r.headers.get("content-type", "")
    return r.json() if "json" in ctype else r


def _image(data: bytes) -> Any:
    """An MCP image result (FastMCP in mcp 1.x, MCPServer in 2.x)."""
    import importlib

    try:
        mod = importlib.import_module("mcp.server.fastmcp")
    except ImportError:
        mod = importlib.import_module("mcp.server.mcpserver")
    return mod.Image(data=data, format="jpeg")


def _clock(t: float) -> str:
    s = int(round(t))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _cut(text: str) -> str:
    if len(text) > MAX_TEXT:
        return text[:MAX_TEXT] + f"\n[cut at {MAX_TEXT} characters: ask for a shorter range]"
    return text


def _tag_line(t: dict[str, Any]) -> str:
    who = ", ".join(p.get("character") or p.get("name", "") for p in t["people"])
    parts = [
        f"{t['id']} {_clock(t['start'])}-{_clock(t['end'])} [{t['status']}] {' '.join(t['tags'])}"
    ]
    if who:
        parts.append(who)
    if t["summary"]:
        parts.append(t["summary"])
    return " | ".join(parts)


# -- tools ---------------------------------------------------------------------------
def list_films(collection: str = "") -> str:
    """List the films in the library (optionally one Plex collection).

    Args:
        collection: A collection name such as "Harry Potter", or "" for every film.

    Returns:
        One film per line: key, title (year), whether it has a scene index, tag counts.
    """
    films = _call(
        "GET", "/api/scenes/films", params={"collection": collection} if collection else None
    )
    rows = [
        f"{f['key']} | {f['title']} ({f['year'] or '?'}) | "
        f"{'indexed' if f['indexed'] else 'not indexed'} | "
        f"{f['tags']['accepted']} accepted, {f['tags']['proposed']} proposed"
        for f in films["films"]
    ]
    return "\n".join(rows) or "No films."


def film_overview(key: str) -> str:
    """The scene index of one film: duration, subtitles, automatic scenes, tags and cast.

    Args:
        key: Film key from list_films.

    Returns:
        A text overview. Automatic scenes show their time range and first line of dialogue.
    """
    d = _call("GET", "/api/scenes/film", params={"key": key, "lines": "true"})
    f, doc = d["film"], d["index"]
    out = [f"{f['title']} ({f['year'] or '?'}) key={f['key']}"]
    if not doc:
        out.append("Not indexed yet: call index_film first.")
        return "\n".join(out)
    sub = doc["subtitles"]
    out.append(
        f"Duration {_clock(doc['duration'] or 0)}; {len(doc['shots'])} shots; "
        f"{len(doc['scenes'])} scenes; subtitles: {sub['kind']} ({sub['lines']} lines)"
    )
    if d["cast"]:
        out.append("Cast: " + "; ".join(f"{c['character']} ({c['name']})" for c in d["cast"][:20]))
    lines = doc.get("lines") or []
    out.append("\nScenes:")
    for sc in doc["scenes"]:
        first = next((ln["text"] for ln in lines if sc["start"] <= ln["start"] < sc["end"]), "")
        out.append(f"{_clock(sc['start'])}-{_clock(sc['end'])} {first[:80]}")
    if d["tags"]:
        out.append("\nTags:")
        out += [_tag_line(t) for t in d["tags"]]
    return _cut("\n".join(out))


def index_film(key: str, transcribe: bool = False) -> str:
    """Start indexing a film (camera cuts, subtitle lines, automatic scenes) as a background job.

    Args:
        key: Film key from list_films.
        transcribe: When the film has no text subtitles, transcribe its audio (needs an STT key).

    Returns:
        The job id; poll it with job_status.
    """
    job = _call("POST", "/api/scenes/index", json={"key": key, "transcribe": transcribe})
    return f"Started job {job['id']}. A 2-hour film takes a few minutes; check job_status."


def job_status(job_id: str) -> str:
    """Status of a background job (indexing, AI tagging).

    Args:
        job_id: The id returned when the job started.
    """
    j = _call("GET", f"/api/jobs/{job_id}")
    msg = f"{j['kind']}: {j['status']} {round((j.get('progress') or 0) * 100)}%"
    if j.get("message"):
        msg += f" ({j['message']})"
    if j.get("error"):
        msg += f"\nError: {j['error']}"
    if j.get("result"):
        msg += f"\nResult: {j['result']}"
    return msg


def read_dialogue(key: str, start: float = 0, end: float = 0) -> str:
    """Timed subtitle lines of a film, as "[h:mm:ss] text", for a range in seconds.

    Args:
        key: Film key.
        start: Start in seconds.
        end: End in seconds (0 = the end of the film). Ask for 10-20 minutes at a time.
    """
    params: dict[str, Any] = {"key": key, "start": start}
    if end:
        params["end"] = end
    lines = _call("GET", "/api/scenes/transcript", params=params)["lines"]
    return (
        _cut("\n".join(f"[{_clock(ln['start'])}] {ln['text']}" for ln in lines))
        or "No dialogue here."
    )


def look(key: str, start: float, end: float, cols: int = 3, rows: int = 2) -> list[Any]:
    """Look at a stretch of a film: frames spread over the range in one labelled grid.

    Each tile shows its number and time (h:mm:ss). Use it to confirm what happens,
    where it is and who is on screen, and to find where a scene starts and ends.

    Args:
        key: Film key.
        start: Start in seconds.
        end: End in seconds.
        cols: Tiles per row (up to 6).
        rows: Rows (up to 6). A 3x2 grid over a 1-3 minute stretch reads well.
    """
    r = _call(
        "GET",
        "/api/scenes/sheet",
        params={"key": key, "start": start, "end": end, "cols": cols, "rows": rows},
    )
    times = [float(x) for x in r.headers.get("x-tile-times", "").split(",") if x]
    legend = " | ".join(f"#{i + 1} {_clock(t)} ({t:.1f}s)" for i, t in enumerate(times))
    return [f"Tiles: {legend}", _image(r.content)]


def frame(key: str, t: float) -> list[Any]:
    """One frame of a film at t seconds.

    Args:
        key: Film key.
        t: Time in seconds.
    """
    r = _call("GET", "/api/scenes/frame", params={"key": key, "t": t, "height": 480})
    return [f"Frame at {_clock(t)}", _image(r.content)]


def camera_cuts(key: str, start: float, end: float) -> str:
    """The shots (camera cuts) inside a range, to set scene edges exactly.

    Args:
        key: Film key.
        start: Start in seconds.
        end: End in seconds.
    """
    shots = _call("GET", "/api/scenes/shots", params={"key": key, "start": start, "end": end})[
        "shots"
    ]
    return "\n".join(f"{a:.2f}-{b:.2f}" for a, b in shots) or "No shots here."


def cast(key: str) -> str:
    """The cast of a film: character (actor), in billing order.

    Args:
        key: Film key.
    """
    people = _call("GET", "/api/scenes/cast", params={"key": key})["cast"]
    return "\n".join(f"{c['character']} ({c['name']})" for c in people) or "No cast known."


def propose_scene(
    key: str,
    start: float,
    end: float,
    tags: list[str],
    people: list[str] | None = None,
    summary: str = "",
    confidence: float = 0.7,
) -> str:
    """Propose a tagged scene. A person accepts or rejects it in the FansCut tagger.

    Edges snap to the nearest camera cut within 3 seconds.

    Args:
        key: Film key.
        start: Start in seconds.
        end: End in seconds.
        tags: Short tags such as ["christmas", "great-hall"].
        people: Characters in the scene, by name ("Harry", "Ron"). Matched to the cast.
        summary: One plain sentence about what happens.
        confidence: 0-1, how sure you are.
    """
    t = _call(
        "POST",
        "/api/scenes/tags",
        json={
            "key": key,
            "start": start,
            "end": end,
            "tags": tags,
            "people": people or [],
            "summary": summary,
            "status": "proposed",
            "source": "mcp",
            "confidence": confidence,
        },
    )
    return "Proposed: " + _tag_line(t)


def list_tags(key: str = "", tag: str = "", status: str = "") -> str:
    """Tagged scenes, filtered by film, tag and status (proposed, accepted, rejected).

    Args:
        key: Film key, or "" for every film.
        tag: A tag, or "" for all.
        status: proposed, accepted, rejected, or "" for all.
    """
    params = {k: v for k, v in (("key", key), ("tag", tag), ("status", status)) if v}
    tags = _call("GET", "/api/scenes/tags", params=params)["tags"]
    return "\n".join(f"{t['film_key']} {_tag_line(t)}" for t in tags) or "No tags."


def update_scene(
    tag_id: str,
    start: float = -1,
    end: float = -1,
    tags: list[str] | None = None,
    people: list[str] | None = None,
    summary: str | None = None,
    status: str = "",
) -> str:
    """Change a tagged scene: its range, tags, people, summary or status.

    Args:
        tag_id: The id shown by list_tags or propose_scene.
        start: New start in seconds (-1 keeps it).
        end: New end in seconds (-1 keeps it).
        tags: New tag list (omit to keep).
        people: New people list (omit to keep).
        summary: New summary (omit to keep).
        status: proposed, accepted or rejected ("" keeps it). Leave accepting to the person
            unless they asked you to.
    """
    body: dict[str, Any] = {}
    if start >= 0:
        body["start"] = start
    if end >= 0:
        body["end"] = end
    if tags is not None:
        body["tags"] = tags
    if people is not None:
        body["people"] = people
    if summary is not None:
        body["summary"] = summary
    if status:
        body["status"] = status
    return "Updated: " + _tag_line(_call("PATCH", f"/api/scenes/tags/{tag_id}", json=body))


def run_ai_tagging(
    key: str, tag: str, description: str = "", verify: bool = True, sweep: bool = False
) -> str:
    """Run FansCut's own AI tagging job on a film (its configured models do the work).

    Args:
        key: Film key (must be indexed).
        tag: The tag to look for, e.g. "christmas".
        description: What the tag means, e.g. "Christmas at Hogwarts: snow, presents, the feast".
        verify: Check each candidate on a frame grid with the vision model.
        sweep: Also look at scenes with no dialogue (one frame grid each; slower).
    """
    job = _call(
        "POST",
        "/api/scenes/ai",
        json={"key": key, "tag": tag, "description": description, "verify": verify, "sweep": sweep},
    )
    return f"Started job {job['id']}; check job_status."


def collection_tags(name: str, tag: str = "") -> str:
    """Tagged scenes across a collection, film by film, plus the tags in use.

    Args:
        name: Collection name, e.g. "Harry Potter".
        tag: Only this tag ("" for all).
    """
    d = _call(
        "GET", "/api/scenes/collection", params={"name": name, **({"tag": tag} if tag else {})}
    )
    out = ["Tags: " + ", ".join(f"{t['tag']} ({t['count']})" for t in d["tags"])]
    for f in d["films"]:
        out.append(
            f"\n{f['title']} ({f['year'] or '?'}) key={f['key']}"
            f"{'' if f['indexed'] else ' [not indexed]'}"
        )
        out += [f"  {_tag_line(t)}" for t in f["scenes"]]
    return _cut("\n".join(out))


def make_timeline(
    collection: str, tag: str, title: str = "", films: list[str] | None = None
) -> str:
    """Turn the accepted scenes with a tag across a collection into a FansCut timeline cut.

    Args:
        collection: Collection name (also names the cut).
        tag: The tag, e.g. "christmas".
        title: Title of the cut (default "<collection>: <tag>").
        films: Film keys to use instead of the collection's films, for a franchise
            whose films aren't all in one Plex collection.
    """
    t = _call(
        "POST",
        "/api/scenes/to-timeline",
        json={"collection": collection, "tag": tag, "title": title, "films": films},
    )
    return f'Created timeline {t["id"]} "{t["title"]}". Open it in FansCut to sync and render.'


TOOLS = [
    list_films,
    film_overview,
    index_film,
    job_status,
    read_dialogue,
    look,
    frame,
    camera_cuts,
    cast,
    propose_scene,
    list_tags,
    update_scene,
    run_ai_tagging,
    collection_tags,
    make_timeline,
]


def tool_definitions() -> list[Any]:
    from prompture.agents.tools_schema import tool_from_function

    images = {look, frame}  # return content blocks, not JSON
    return [
        tool_from_function(
            fn,
            name=fn.__name__,
            metadata={
                "category": "fanscut",
                **({"structured_output": False} if fn in images else {}),
            },
        )
        for fn in TOOLS
    ]


def serve(url: str | None = None) -> None:
    """Run the MCP server on stdio (blocking)."""
    if url:
        os.environ["FANSCUT_URL"] = url
    from prompture.mcp.server import serve as prompture_serve

    prompture_serve(name="fanscut", tools=tool_definitions(), transport="stdio")

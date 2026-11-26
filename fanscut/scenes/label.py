"""One-pass labelling: every content tag of the list in one read of the film.

1. **Read.** The dialogue, in the same windows as the single-tag pass, goes to
   the text model once with the whole tag list (the families that are on).
   It returns stretches, each with the tag ids that apply. The reply schema
   only allows ids from the list.
2. **Look.** Each stretch is shown to the vision model on one contact sheet:
   it keeps the tags it can confirm, may add ones plainly visible, trims the
   range to the matching tiles and names who is on screen.
3. **Sweep** (optional). Stretches with almost no dialogue go straight to the
   look step with no tags, so places, weather and action without words are
   found too.

Everything is saved as proposals, source ``ai:<model>``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field, create_model

from .. import ai
from ..config import AICfg
from . import tagger, vocab

MAX_TAGS = 12  # per moment (marker file limit)
MIN_LEN = 3.0
OVERLAP = 0.5  # candidates sharing this much of the shorter one become one


def tag_list(families: list[str]) -> list[dict[str, Any]]:
    off = [f for f in vocab.families() if f not in families]
    return vocab.content_tags(off)


def schemas(ids: list[str]) -> tuple[type[BaseModel], type[BaseModel]]:
    """Reply models whose tag fields only accept *ids*."""
    tag_id = Literal[tuple(ids)]  # type: ignore[valid-type]
    moment = create_model(
        "Moment",
        window=(int, Field(description="window number as given")),
        start=(str, Field(description="where it starts, h:mm:ss of the film")),
        end=(str, Field(description="where it ends, h:mm:ss of the film")),
        tags=(list[tag_id], Field(description="tag ids from the list that apply")),
        people=(list[str], Field(default_factory=list, description="characters in it")),
        why=(str, Field(default="", description="a few words on what happens")),
        confidence=(float, Field(default=0.5, ge=0, le=1)),
    )
    moments = create_model("Moments", moments=(list[moment], Field(default_factory=list)))
    look = create_model(
        "LookCheck",
        tags=(list[tag_id], Field(description="tag ids you can confirm on the sheet")),
        first_tile=(int, Field(default=1, description="first tile that belongs")),
        last_tile=(int, Field(default=6, description="last tile that belongs")),
        summary=(str, Field(default="", description="one plain sentence on what happens")),
        people=(list[str], Field(default_factory=list, description="characters you can see")),
        confidence=(float, Field(default=0.5, ge=0, le=1)),
    )
    return moments, look


TEXT_SYSTEM = """\
You tag a film from its subtitles with a fixed list of tags. You get the list
(id: meaning, grouped by family) and numbered windows of dialogue with times
(h:mm:ss of the film). Return the stretches of the film where tags clearly
apply, each with where it starts and ends inside its window and every tag id
from the list that applies to it. A stretch is one scene or beat, from a few
seconds to a few minutes. Judge by what happens and where, not by a single
word. Mood tags only when the mood is strong and plain. Use only ids from the
list. If nothing applies, return an empty list. Never invent windows.
"""

LOOK_SYSTEM = """\
You check one stretch of a film on a contact sheet: numbered tiles in reading
order, each labelled with its time. You get tags someone proposed from the
dialogue and the full tag list. Keep the proposed tags you can confirm, drop
the rest, and add tags from the list that are plainly visible (places,
weather, time of day, creatures, actions). Give the first and last tile the
tags belong to, one plain sentence on what happens, and the characters you
can see (use names from the cast list when you can tell). Be strict: a place
or weather tag must be visible. If no tag applies, return no tags.
"""


def list_text(tags: list[dict[str, Any]]) -> str:
    by: dict[str, list[str]] = {}
    for t in tags:
        by.setdefault(t["family"], []).append(f"{t['id']}: {t['hint']}")
    return "\n".join(f"[{fam}]\n" + "\n".join(rows) for fam, rows in by.items())


def read(
    cfg: AICfg,
    doc: dict[str, Any],
    title: str,
    tags: list[dict[str, Any]],
    cast: list[dict[str, Any]],
    cancel: Any = None,
    progress: Callable[[float, str], None] | None = None,
) -> list[dict[str, Any]]:
    """Candidate stretches from the dialogue: [{start, end, tags, why, people, confidence}]."""
    ids = [t["id"] for t in tags]
    moments_model, _ = schemas(ids)
    wins = tagger.windows(doc)
    groups = tagger.batches(doc, wins)
    head = f"Film: {title}\nCast: {tagger.cast_hint(cast)}\n\nTags:\n{list_text(tags)}\n\n"
    found: list[dict[str, Any]] = []
    for gi, group in enumerate(groups):
        if cancel is not None and cancel.is_set():
            break
        if progress:
            progress(gi / max(1, len(groups)), f"Reading dialogue {gi + 1}/{len(groups)}")
        parts = []
        for i in group:
            text = tagger.window_text(doc, wins[i])
            if text:
                parts.append(
                    f"## Window {i} ({tagger._clock(wins[i][0])}-{tagger._clock(wins[i][1])})\n"
                    + text
                )
        if not parts:
            continue
        reply = ai.ask_json(cfg, moments_model, TEXT_SYSTEM, head + "\n\n".join(parts))
        for m in reply.moments:
            if m.window not in group or not m.tags:
                continue
            lo, hi = wins[m.window]
            a, b = tagger._parse(m.start, lo, hi), tagger._parse(m.end, lo, hi)
            if b - a < 5:
                a, b = max(lo, a - 15), min(hi, a + 45)
            found.append(
                {
                    "start": a,
                    "end": b,
                    "tags": [t for t in dict.fromkeys(m.tags) if t in ids],
                    "why": m.why,
                    "people": list(m.people),
                    "confidence": m.confidence,
                }
            )
    return found


def merge(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates that mostly overlap become one with the union of their tags."""
    out: list[dict[str, Any]] = []
    for c in sorted(cands, key=lambda c: (c["start"], c["end"])):
        last = out[-1] if out else None
        if last is not None:
            shared = min(last["end"], c["end"]) - max(last["start"], c["start"])
            shorter = min(last["end"] - last["start"], c["end"] - c["start"]) or 1
            if shared / shorter >= OVERLAP:
                last["start"], last["end"] = (
                    min(last["start"], c["start"]),
                    max(last["end"], c["end"]),
                )
                last["tags"] = list(dict.fromkeys([*last["tags"], *c["tags"]]))
                last["people"] = list(dict.fromkeys([*last["people"], *c["people"]]))
                last["confidence"] = max(last["confidence"], c["confidence"])
                last["why"] = last["why"] or c["why"]
                continue
        out.append({**c, "tags": list(c["tags"]), "people": list(c["people"])})
    return out


def look(
    cfg: AICfg,
    sheet: Any,
    title: str,
    cand: dict[str, Any],
    tags: list[dict[str, Any]],
    cast: list[dict[str, Any]],
) -> Any:
    _, look_model = schemas([t["id"] for t in tags])
    prompt = (
        f"Film: {title}\nCast: {tagger.cast_hint(cast)}\n"
        f"Proposed: {', '.join(cand['tags']) or '(none: tag what you see)'}\n"
        f"From the dialogue: {cand.get('why') or '-'}\n\nTags:\n{list_text(tags)}\n\n"
        f"Tiles: {sheet.legend()}"
    )
    return ai.ask_json(cfg, look_model, LOOK_SYSTEM, prompt, images=[sheet.data])

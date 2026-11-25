"""Turn community cut notes into a recipe with an OpenAI-compatible LLM.

People who publish fan edits often describe their cuts — episode lists,
timestamps, rationale — without sharing video. This module hands those notes,
plus the episode list of the local series, to a chat-completion endpoint and
validates whatever comes back as a FansCut recipe. The recipe is the contract:
if it doesn't validate, nothing is applied.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

from . import recipe as recipe_mod
from .config import AICfg
from .errors import FansCutError
from .models import Episode, Series

SYSTEM = """\
You turn fan-edit notes into a FansCut recipe (JSON, no prose, no markdown).

Recipe shape:
{"schema": 2, "series": {"title": "...", "mal_id": null, "anilist_id": null},
 "preset": "custom",
 "episodes": {"S01E02": {"include": false},
              "S01E04": {"segments": [[start, end], ...]},
              "S01E05": {"trims": {"intro": [start, end]}}}}

Rules:
- Only use episode ids from the supplied list, exactly as given ("S01E04").
- "include": false drops a whole episode; true forces it kept.
- "segments" are the kept parts of an episode in seconds; use them for partial
  keeps (favorite scenes, events to keep). Anything not listed is dropped.
- "trims" are ranges to remove; kinds are intro, outro, recap, preview.
- All times are seconds from the start of that episode's file. Never exceed the
  episode's duration.
- When the notes give times as mm:ss or hh:mm:ss, convert to seconds.
- If the notes say nothing about an episode, leave it out of "episodes".
- Output the JSON object and nothing else.
"""


class AIError(FansCutError):
    pass


def extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in an LLM answer (code fences and chatter tolerated)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("{")
    if start < 0:
        raise AIError("the model returned no JSON object")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    doc = json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise AIError(f"the model returned invalid JSON: {exc}") from exc
                if isinstance(doc, dict):
                    return doc
                raise AIError("the model returned JSON that is not an object")
    raise AIError("the model returned unbalanced JSON")


def build_messages(series: Series, episodes: list[Episode], notes: str) -> list[dict[str, str]]:
    listing = "\n".join(
        f"- {e.id} (season {e.season}, episode {e.number})"
        f"{f' — {e.title}' if e.title else ''}"
        f"{f', {round(e.duration)}s' if e.duration else ''}" for e in episodes)
    user = (f"Series: {series.title}\n\nEpisodes:\n{listing}\n\n"
            f"Fan-edit notes:\n{notes.strip()}")
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def chat(cfg: AICfg, messages: list[dict[str, str]],
         session: requests.Session | None = None) -> str:
    """One chat completion against an OpenAI-compatible endpoint."""
    if not cfg.base_url or not cfg.model:
        raise AIError("ai.base_url and ai.model are not configured (see Settings or config.yaml)")
    http = session or requests
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    try:
        r = http.post(f"{cfg.base_url.rstrip('/')}/chat/completions",
                      json={"model": cfg.model, "messages": messages, "temperature": 0.2},
                      headers=headers, timeout=cfg.timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise AIError(f"LLM request failed: {exc}") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIError(f"unexpected response from the LLM endpoint: {exc}") from exc


def generate_recipe(cfg: AICfg, series: Series, episodes: list[Episode], notes: str,
                    session: requests.Session | None = None) -> dict[str, Any]:
    """Notes in -> validated recipe dict out. Raises AIError/RecipeError."""
    if not notes.strip():
        raise AIError("no notes given")
    answer = chat(cfg, build_messages(series, episodes, notes), session)
    return recipe_mod.validate(extract_json(answer))


# -- timeline outlines (the AI orders scenes; humans set exact times) ---------------
OUTLINE_SYSTEM = """\
You turn chronology notes into a scene outline for a chronological fan edit.
The outline is JSON, no prose, no markdown:

[{"part": "Year -1003",
  "scenes": [{"src": "S04E21", "approx": "05:10", "desc": "Ymir in the tree"}]}]

Rules:
- "part" is a chapter of the edit, in story order.
- "src" is an episode id from the supplied list, exactly as given ("S04E21";
  OVAs and specials are S00Exx).
- "approx" is where the scene roughly starts in that episode, mm:ss or hh:mm:ss.
  Never invent precise times: the user confirms the real in/out points later.
- "desc" is a few plain words about the scene, no episode synopses.
- Output the JSON array and nothing else.
"""

APPROX_RE = re.compile(r"^(?:(\d+):)?([0-5]?\d):([0-5]\d)$")


def parse_approx(s: Any) -> float:
    if not isinstance(s, str) or not (m := APPROX_RE.match(s.strip())):
        raise AIError(f"bad approx time {s!r} (want mm:ss)")
    return float(int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))


def validate_outline(doc: Any) -> list[dict[str, Any]]:
    if not isinstance(doc, list) or not doc:
        raise AIError("the model returned no outline")
    out = []
    for i, part in enumerate(doc[:200], start=1):
        if not isinstance(part, dict) or not isinstance(part.get("part"), str):
            raise AIError(f"outline part {i}: expected an object with a title")
        scenes = []
        for j, sc in enumerate((part.get("scenes") or [])[:500], start=1):
            if not isinstance(sc, dict) or not isinstance(sc.get("src"), str):
                raise AIError(f"outline part {i} scene {j}: expected an object with src")
            entry: dict[str, Any] = {"src": sc["src"],
                                     "approx": round(parse_approx(sc.get("approx")), 1)}
            if isinstance(sc.get("desc"), str):
                entry["desc"] = sc["desc"][:300]
            scenes.append(entry)
        out.append({"part": part["part"][:200], "scenes": scenes})
    return out


def generate_outline(cfg: AICfg, series: Series, episodes: list[Episode], notes: str,
                     session: requests.Session | None = None) -> list[dict[str, Any]]:
    """Chronology notes in -> scene outline out. Raises AIError."""
    if not notes.strip():
        raise AIError("no notes given")
    listing = "\n".join(f"- {e.id}{f' — {e.title}' if e.title else ''}" for e in episodes)
    messages = [{"role": "system", "content": OUTLINE_SYSTEM},
                {"role": "user", "content": f"Series: {series.title}\n\nEpisodes:\n{listing}\n\n"
                                            f"Chronology notes:\n{notes.strip()}"}]
    return validate_outline(extract_json_list(chat(cfg, messages, session)))


def extract_json_list(text: str) -> Any:
    """A JSON array from an LLM answer (fences and chatter tolerated)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("[")
    if start < 0:
        raise AIError("the model returned no JSON array")
    try:
        return json.loads(text[start:text.rfind("]") + 1])
    except json.JSONDecodeError as exc:
        raise AIError(f"the model returned invalid JSON: {exc}") from exc

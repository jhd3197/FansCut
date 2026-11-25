"""Language-model calls, through Prompture.

People who publish fan edits often describe their cuts — episode lists,
timestamps, rationale — without sharing video. This module hands those notes,
plus the episode list of the local series, to a model and validates whatever
comes back as a FansCut recipe. The recipe is the contract: if it doesn't
validate, nothing is applied.

The model is a Prompture model string ("claude/claude-opus-5-5",
"ollama/qwen3-vl:8b", "google/gemini-2.5-flash"). With ``ai.base_url`` set and
a bare model name, the endpoint is treated as OpenAI-compatible (Ollama's /v1,
LM Studio, a gateway), which is how FansCut was configured before Prompture.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

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


def model_spec(cfg: AICfg, *, vision: bool = False) -> tuple[str, dict[str, Any]]:
    """The Prompture model string and driver overrides for this config."""
    model = (cfg.vision_model if vision and cfg.vision_model else cfg.model).strip()
    if not model:
        raise AIError("ai.model is not configured (see Settings or config.yaml)")
    overrides: dict[str, Any] = {}
    provider = model.split("/", 1)[0] if "/" in model else ""
    if cfg.base_url and not provider:
        # A bare name on a custom endpoint: OpenAI-compatible chat completions.
        model = f"openai/{model}"
        overrides["base_url"] = cfg.base_url.rstrip("/")
        overrides["api_key"] = cfg.api_key or "not-needed"
    elif not provider:
        raise AIError(f"ai.model {model!r} needs a provider prefix such as ollama/, claude/ or "
                      "google/ (or set ai.base_url for an OpenAI-compatible endpoint)")
    else:
        if cfg.api_key:
            overrides["api_key"] = cfg.api_key
        if cfg.base_url and provider == "ollama":
            overrides["endpoint"] = cfg.base_url
    return model, overrides


def driver_for(cfg: AICfg, *, vision: bool = False) -> Any:
    """A Prompture driver for the configured model."""
    model, overrides = model_spec(cfg, vision=vision)
    try:
        import prompture
    except ImportError as exc:  # pragma: no cover - prompture is a dependency
        raise AIError("Prompture is not installed (pip install prompture)") from exc
    try:
        return prompture.get_driver_for_model(model, **overrides)
    except Exception as exc:  # noqa: BLE001 - any init failure means "unavailable"
        raise AIError(f"could not start {model}: {exc}") from exc


def chat(cfg: AICfg, messages: list[dict[str, Any]], *, vision: bool = False) -> str:
    """One chat completion; returns the reply text."""
    driver = driver_for(cfg, vision=vision)
    try:
        out = driver.generate_messages(messages, {"temperature": 0.2, "timeout": cfg.timeout})
    except Exception as exc:  # noqa: BLE001 - transport failures
        raise AIError(f"LLM request failed: {exc}") from exc
    text = out.get("text") if isinstance(out, dict) else None
    if not isinstance(text, str):
        raise AIError("unexpected response from the model (no text)")
    return text


def ask_json(cfg: AICfg, schema: type[BaseModel], system: str, prompt: str, *,
             images: list[Any] | None = None, retries: int = 1, max_tokens: int = 4096) -> Any:
    """A validated instance of *schema*; *images* (bytes or paths) go to a vision model.

    An invalid reply is retried once with the validation error as feedback,
    then reported as AIError (never repaired into a guess).
    """
    import prompture
    from prompture.exceptions import ExtractionError

    vision = bool(images)
    driver = driver_for(cfg, vision=vision)
    model, _ = model_spec(cfg, vision=vision)
    feedback = ""
    last: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            result = prompture.ask_for_json(
                driver=driver, content_prompt=prompt + feedback,
                json_schema=schema.model_json_schema(), system_prompt=system,
                model_name=model.split("/", 1)[-1],
                options={"timeout": cfg.timeout, "max_tokens": max_tokens, "temperature": 0.2},
                ai_cleanup=False, cache=False, images=images or None)
            return schema.model_validate(result["json_object"])
        except (ExtractionError, ValidationError, KeyError, TypeError) as exc:
            last = exc
            feedback = (f"\nThe previous answer was invalid: {str(exc)[:300]}. "
                        "Return JSON matching the schema exactly.")
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures
            raise AIError(f"{model} failed: {exc}") from exc
    raise AIError(f"{model} returned invalid JSON after {retries + 1} tries: {last}")


def generate_recipe(cfg: AICfg, series: Series, episodes: list[Episode], notes: str
                    ) -> dict[str, Any]:
    """Notes in -> validated recipe dict out. Raises AIError/RecipeError."""
    if not notes.strip():
        raise AIError("no notes given")
    answer = chat(cfg, build_messages(series, episodes, notes))
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


def generate_outline(cfg: AICfg, series: Series, episodes: list[Episode], notes: str
                     ) -> list[dict[str, Any]]:
    """Chronology notes in -> scene outline out. Raises AIError."""
    if not notes.strip():
        raise AIError("no notes given")
    listing = "\n".join(f"- {e.id}{f' — {e.title}' if e.title else ''}" for e in episodes)
    messages = [{"role": "system", "content": OUTLINE_SYSTEM},
                {"role": "user", "content": f"Series: {series.title}\n\nEpisodes:\n{listing}\n\n"
                                            f"Chronology notes:\n{notes.strip()}"}]
    return validate_outline(extract_json_list(chat(cfg, messages)))


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

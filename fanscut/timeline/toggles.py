"""Toggles and variants: optional scenes that depend on optional sources.

A toggle switches on the segments whose `when` names it ("lotr-open") and
switches off the ones marked "!lotr-open". A toggle whose `needs` sources
aren't in the library is disabled and names the film it needs: never an
error. A variant is a named set of toggle values; anything it doesn't set
keeps its default.
"""

from __future__ import annotations

from typing import Any

from .recipe import TimelineError


def source_label(doc: dict[str, Any], src: str) -> str:
    s = doc["sources"].get(src, {})
    name = s.get("note") or src
    extra = [x for x in ((src if s.get("note") else ""), s.get("edition", "")) if x]
    return f"{name} ({', '.join(extra)})" if extra else name


def resolve(doc: dict[str, Any], available: set[str], variant: str | None = None,
            chosen: dict[str, bool] | None = None
            ) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Toggle values for a render, plus a per-toggle view (enabled, reason)."""
    values = {t["id"]: t["default"] for t in doc.get("toggles", [])}
    if variant:
        v = next((v for v in doc.get("variants", []) if v["id"] == variant), None)
        if v is None:
            raise TimelineError(f"unknown variant {variant!r}")
        values.update(v["toggles"])
    for k, on in (chosen or {}).items():
        if k not in values:
            raise TimelineError(f"unknown toggle {k!r}")
        values[k] = bool(on)
    view = []
    for t in doc.get("toggles", []):
        gone = [n for n in t["needs"] if n not in available]
        if gone:
            values[t["id"]] = False
        view.append({"id": t["id"], "label": t["label"], "default": t["default"],
                     "on": values[t["id"]], "enabled": not gone,
                     "reason": ("needs " + ", ".join(source_label(doc, n) for n in gone))
                     if gone else ""})
    return values, view


def active(seg: dict[str, Any], values: dict[str, bool]) -> bool:
    when = seg.get("when")
    if not when:
        return True
    if when.startswith("!"):
        return not values.get(when[1:], False)
    return values.get(when, False)


def off_set(doc: dict[str, Any], values: dict[str, bool]) -> set[tuple[int, int]]:
    """(part, segment), 1-based, of the segments the toggles switch off."""
    return {(pi, si) for pi, part in enumerate(doc["parts"], start=1)
            for si, seg in enumerate(part["segments"], start=1) if not active(seg, values)}


def runtime(doc: dict[str, Any], values: dict[str, bool],
            skip: set[tuple[int, int]] | None = None) -> float:
    off = off_set(doc, values) | (skip or set())
    return sum(seg["end"] - seg["start"] for pi, part in enumerate(doc["parts"], start=1)
               for si, seg in enumerate(part["segments"], start=1) if (pi, si) not in off)


def variant_label(doc: dict[str, Any], variant: str | None) -> str:
    if not variant:
        return ""
    v = next((v for v in doc.get("variants", []) if v["id"] == variant), None)
    if v is None:
        raise TimelineError(f"unknown variant {variant!r}")
    return str(v["label"])


def variants_view(doc: dict[str, Any], available: set[str],
                  skip: set[tuple[int, int]] | None = None) -> dict[str, Any]:
    """Every variant (plus the defaults) with its runtime and any blocked toggles."""
    rows = []
    for v in [{"id": "", "label": "Default", "toggles": {}}, *doc.get("variants", [])]:
        values, view = resolve(doc, available, v["id"] or None)
        wanted = {t["id"]: t["default"] for t in doc.get("toggles", [])} | v["toggles"]
        blocked = [t for t in view if not t["enabled"] and wanted.get(t["id"])]
        rows.append({"id": v["id"], "label": v["label"], "toggles": values,
                     "description": v.get("description", ""),
                     "runtime": round(runtime(doc, values, skip), 1),
                     "complete": not blocked,
                     "blocked": [{"id": t["id"], "label": t["label"], "reason": t["reason"]}
                                 for t in blocked]})
    _vals, view = resolve(doc, available)
    return {"toggles": view, "variants": rows}

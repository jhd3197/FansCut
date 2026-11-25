"""Toggles and variants (Took: Hobbit trilogy + optional LOTR bookends)."""

from __future__ import annotations

import copy

import pytest

from fanscut.timeline import recipe, toggles
from fanscut.timeline import render as tl_render

TOOK = {
    "schema": 2, "kind": "timeline", "title": "Took", "series": {"title": "Took"},
    "sources": {
        "M49051": {"kind": "movie", "tmdb": 49051, "duration": 10947.0,
                   "note": "An Unexpected Journey", "edition": "extended"},
        "M120": {"kind": "movie", "tmdb": 120, "duration": 13695.0, "optional": True,
                 "note": "The Fellowship of the Ring", "edition": "extended"},
    },
    "toggles": [{"id": "lotr-open", "label": "Open with Fellowship's prologue",
                 "default": True, "needs": ["M120"]}],
    "variants": [{"id": "book", "label": "Book Order", "toggles": {"lotr-open": False}},
                 {"id": "saga", "label": "Saga", "toggles": {"lotr-open": True}}],
    "parts": [{"title": "Took", "segments": [
        {"src": "M120", "start": 0.0, "end": 400.0, "when": "lotr-open"},
        {"src": "M49051", "start": 0.0, "end": 60.0, "when": "!lotr-open"},
        {"src": "M49051", "start": 600.0, "end": 1600.0},
    ]}],
}


def doc():
    return recipe.validate(copy.deepcopy(TOOK))


def test_validates_and_keeps_toggles_and_variants():
    d = doc()
    assert d["sources"]["M120"]["optional"] is True
    assert d["toggles"][0]["needs"] == ["M120"]
    assert [v["id"] for v in d["variants"]] == ["book", "saga"]


@pytest.mark.parametrize("mut, msg", [
    (lambda d: d["parts"][0]["segments"][0].pop("when"), "needs a toggle"),
    (lambda d: d["parts"][0]["segments"][0].update(when="!lotr-open"), "needs a toggle"),
    (lambda d: d["parts"][0]["segments"][1].update(when="nope"), "unknown toggle"),
    (lambda d: d["toggles"][0].update(id="Bad Id"), "lowercase"),
    (lambda d: d["toggles"][0].update(needs=["M999"]), "needs"),
    (lambda d: d["toggles"][0].update(default="yes"), "default"),
    (lambda d: d["variants"][0]["toggles"].update(other=True), "unknown toggle"),
    (lambda d: d["variants"].append(dict(d["variants"][0])), "unique id"),
])
def test_bad_toggles_rejected(mut, msg):
    d = copy.deepcopy(TOOK)
    mut(d)
    with pytest.raises(recipe.TimelineError, match=msg):
        recipe.validate(d)


def test_missing_optional_source_disables_toggle_and_names_film():
    values, view = toggles.resolve(doc(), available={"M49051"}, variant="saga")
    assert values == {"lotr-open": False}
    assert view[0]["enabled"] is False
    assert "The Fellowship of the Ring" in view[0]["reason"]
    assert "extended" in view[0]["reason"]


def test_runtime_per_variant():
    v = toggles.variants_view(doc(), available={"M49051", "M120"})
    rt = {r["id"]: r["runtime"] for r in v["variants"]}
    assert rt == {"": 1400.0, "book": 1060.0, "saga": 1400.0}
    assert all(r["complete"] for r in v["variants"])


def test_variant_blocked_when_film_missing():
    v = toggles.variants_view(doc(), available={"M49051"})
    saga = next(r for r in v["variants"] if r["id"] == "saga")
    assert not saga["complete"] and "Fellowship" in saga["blocked"][0]["reason"]
    assert saga["runtime"] == 1060.0  # renders without the bookend, never an error


def test_off_segments_are_dropped_before_file_resolution():
    d = doc()
    values, _ = toggles.resolve(d, available={"M49051"})
    off = toggles.off_set(d, values)
    files = {"M49051": ("/m/auj.mkv", 10947.0), "M120": (None, None)}
    segs, warns = tl_render.part_segments(d, 0, files, {}, set(), off)
    assert [(s.start, s.end) for s in segs] == [(0.0, 60.0), (600.0, 1600.0)]
    assert not warns


def test_unknown_variant_rejected():
    with pytest.raises(recipe.TimelineError):
        toggles.resolve(doc(), set(), "nope")


def test_variant_in_output_name():
    show = tl_render.show_name("Took", "Took")
    assert tl_render.with_variant(show, "Book Order").endswith("- Book Order")
    assert tl_render.with_variant(show, "") == show

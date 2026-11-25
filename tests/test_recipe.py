import pytest

from fanscut import recipe


def good(**over):
    doc = {"schema": 1, "series": {"title": "Harbor Lights", "mal_id": 900001},
           "preset": "canon_mixed", "options": {"pad": 1.0},
           "episodes": {"S01E02": {"label": "canon", "include": False,
                                   "trims": {"intro": [60, 150], "outro": None}}}}
    return {**doc, **over}


def test_validate_round_trip():
    clean = recipe.validate(good())
    assert clean["preset"] == "canon_mixed"
    assert clean["episodes"]["S01E02"]["trims"] == {"intro": [60.0, 150.0], "outro": None}


def test_build_merges_overrides_and_trims():
    doc = recipe.build("Harbor Lights", 900001, None, {"preset": "canon_only", "pad": 0.5, "x": 1},
                       {"S01E01": {"label": None, "include": True}},
                       {"S01E01": {"intro": (1.0, 2.0)}, "S01E03": {"outro": None}})
    assert doc["schema"] == 2 and doc["options"] == {"pad": 0.5}
    assert doc["episodes"]["S01E01"] == {"include": True, "trims": {"intro": [1.0, 2.0]}}
    assert doc["episodes"]["S01E03"]["trims"] == {"outro": None}
    assert recipe.validate(doc)  # what we export, we accept


def test_build_includes_custom_segments():
    doc = recipe.build("Harbor Lights", 900001, None, {"preset": "custom"}, {},
                       {}, {"S01E04": [[10.0, 95.5], [300.0, 320.0]]})
    assert doc["episodes"]["S01E04"]["segments"] == [[10.0, 95.5], [300.0, 320.0]]
    clean = recipe.validate(doc)
    assert clean["episodes"]["S01E04"]["segments"] == [[10.0, 95.5], [300.0, 320.0]]
    # a null segments entry is a deliberate "cleared" and round-trips
    doc["episodes"]["S01E04"]["segments"] = None
    assert recipe.validate(doc)["episodes"]["S01E04"]["segments"] is None


def test_segments_validate_and_sort():
    doc = good(schema=2, episodes={"S01E01": {"segments": [[300, 320], [10, 95]]}})
    clean = recipe.validate(doc)
    assert clean["episodes"]["S01E01"]["segments"] == [[10.0, 95.0], [300.0, 320.0]]


@pytest.mark.parametrize("doc", [
    [], {"schema": 3}, good(preset="bogus"), good(options=[]), good(series=[]), good(options={"nope": 1}),
    good(episodes={"S01E01": {"label": "epic"}}),
    good(episodes={"S01E01": {"include": "yes"}}),
    good(episodes={"S01E01": {"trims": {"credits": [1, 2]}}}),
    good(episodes={"S01E01": {"trims": {"intro": [5, 5]}}}),
    good(episodes={"S01E01": {"trims": {"intro": [-1, 5]}}}),
    good(episodes={"S01E01": {"trims": {"intro": [1]}}}),
    good(episodes={"S01E01": {"trims": {"intro": ["a", "b"]}}}),
    good(episodes={"S01E01": {"segments": "soon"}}),
    good(episodes={"S01E01": {"segments": [[95, 10]]}}),
    good(episodes={"S01E01": {"segments": [[-1, 10]]}}),
    good(episodes={"S01E01": "x"}),
])
def test_invalid_recipes_rejected(doc):
    with pytest.raises(recipe.RecipeError):
        recipe.validate(doc)

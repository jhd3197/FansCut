"""AI recipe generation: JSON extraction, endpoint plumbing, validation gate."""

import json

import pytest
import responses

from fanscut import ai
from fanscut.config import AICfg
from fanscut.models import Episode, Series
from tests.conftest import SERIES
from tests.test_api import E, episodes

LLM = "http://ollama.test:11434/v1"

RECIPE = {"schema": 2, "series": {"title": "Harbor Lights"}, "preset": "custom",
          "episodes": {"S01E02": {"include": False},
                       "S01E04": {"segments": [[10, 95]]}}}


def test_extract_json_tolerates_fences_and_chatter():
    assert ai.extract_json('{"a": 1}') == {"a": 1}
    fenced = f"Here you go!\n```json\n{RECIPE}\n```\nHope that helps."
    fenced = fenced.replace(str(RECIPE), json.dumps(RECIPE))
    assert ai.extract_json(fenced)["schema"] == 2
    with pytest.raises(ai.AIError):
        ai.extract_json("no json here")
    with pytest.raises(ai.AIError):
        ai.extract_json("{broken")


def _series():
    return Series(key=SERIES, source="sonarr", title="Harbor Lights")


def _episodes():
    return [Episode(season=1, number=n, title=f"Episode {n}", duration=1440.0)
            for n in range(1, 7)]


def test_generate_recipe_validates_llm_output():
    cfg = AICfg(base_url=LLM, model="test-model")
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, f"{LLM}/chat/completions",
                 json={"choices": [{"message": {"content": json.dumps(RECIPE)}}]})
        doc = ai.generate_recipe(cfg, _series(), _episodes(), "drop ep 2; keep 0:10-1:35 of ep 4")
        assert json.loads(rsps.calls[0].request.body)["model"] == "test-model"
    assert doc["episodes"]["S01E04"]["segments"] == [[10.0, 95.0]]


def test_generate_recipe_rejects_bad_llm_output():
    cfg = AICfg(base_url=LLM, model="test-model")
    bad = {"schema": 2, "episodes": {"S01E01": {"label": "epic"}}}
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, f"{LLM}/chat/completions",
                 json={"choices": [{"message": {"content": json.dumps(bad)}}]})
        with pytest.raises(Exception, match="label"):
            ai.generate_recipe(cfg, _series(), _episodes(), "notes")


def test_unconfigured_or_empty_input():
    with pytest.raises(ai.AIError, match="not configured"):
        ai.generate_recipe(AICfg(), _series(), _episodes(), "notes")
    with pytest.raises(ai.AIError, match="no notes"):
        ai.generate_recipe(AICfg(base_url=LLM, model="m"), _series(), _episodes(), "  ")


def test_ai_recipe_endpoint(client, app_cfg):
    app_cfg.ai.base_url = LLM
    app_cfg.ai.model = "test-model"
    client.get(f"{E}/episodes")  # warm the 30s library cache before the nested mock blocks Sonarr
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.POST, f"{LLM}/chat/completions",
                 json={"choices": [{"message": {"content": json.dumps(RECIPE)}}]})
        r = client.post(f"{E}/recipe/ai", json={"notes": "drop episode 2, keep 0:10-1:35 of ep 4"})
        assert r.status_code == 200
        assert r.json()["recipe"]["episodes"]["S01E04"]["segments"] == [[10.0, 95.0]]
        assert "import" not in r.json()
        # apply=true imports it: episode 2 excluded, episode 4 has segments
        r2 = client.post(f"{E}/recipe/ai", json={"notes": "same", "apply": True})
        assert r2.json()["import"] == {"applied": 2, "skipped": []}
    eps = episodes(client)
    assert eps["S01E02"]["include"] is False
    assert eps["S01E04"]["segments"] == [[10.0, 95.0]]

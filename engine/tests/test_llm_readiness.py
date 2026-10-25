"""Readiness for real models: the prompts the engine sends (recorded live on mock rounds, and the
saved fixtures that `python -m engine.llm_readiness --replay` sends to Bitdeer)."""

import json
from pathlib import Path

import pytest

from contracts.config import MODEL_ROLES
from engine.brains import DATA_RULE
from engine.llm_readiness import CONTEXT_BUDGET_TOKENS, record, replay, schemas

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"


@pytest.fixture(scope="module")
def calls():
    records, _ = record(rounds=4, seed=42)
    return records


def test_every_role_the_engine_calls_has_an_assigned_model(calls):
    assert {c["role"] for c in calls} <= set(MODEL_ROLES)


def test_every_prompt_fits_the_context_budget(calls):
    assert max(c["input_tokens"] for c in calls) < CONTEXT_BUDGET_TOKENS


def test_every_prompt_names_its_answer_fields_and_quotes_its_data(calls):
    by_name = schemas()
    for c in calls:
        system, user = c["messages"][0]["content"], c["messages"][-1]["content"]
        assert DATA_RULE in system
        missing = [f for f in by_name[c["schema"]].model_fields if f not in system]
        assert not missing, f"{c['role']}: the prompt never names {missing}"
        body = user.removeprefix("<data>\n").removesuffix("\n</data>")
        assert "<" not in body  # quoted text can't close the data block early


def test_saved_fixtures_cover_every_role_and_replay_cleanly(calls, monkeypatch):
    import integrations.bitdeer
    from engine.brains import MockBrains

    saved = {p.stem for p in FIXTURES.glob("*.json")}
    assert {f"{c['role']}__{c['schema']}" for c in calls} <= saved
    by_name = schemas()
    for path in FIXTURES.glob("*.json"):  # a fixture whose prompt no longer names its fields is stale
        r = json.loads(path.read_text())
        assert all(f in r["messages"][0]["content"] for f in by_name[r["schema"]].model_fields), \
            f"{path.name} is stale: regenerate with python -m engine.llm_readiness --fixtures {FIXTURES}"
    monkeypatch.setattr(integrations.bitdeer, "BitdeerLLM", MockBrains)
    assert replay(FIXTURES) == 0

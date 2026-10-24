import os
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from contracts import config, conformance, schemas, stubs
from contracts.llm import LLMClient, Message, MockLLM, mock_instance

DB_SQL = Path(__file__).resolve().parent.parent / "db.sql"


def test_stubs_pass_conformance():
    conformance.check_voter(stubs.StubVoter())
    conformance.check_attacker(stubs.StubAttacker())
    conformance.check_page_scanner(stubs.StubPageScanner())
    conformance.check_threat_intel(stubs.StubThreatIntel())
    conformance.check_settlement(stubs.StubSettlement())
    conformance.check_defense(stubs.StubDefense())
    conformance.check_engine(stubs.StubEngine(allowlist={"team"}), team_sender="team")


def test_http_api_round_trips_every_entry_point():
    from fastapi.testclient import TestClient

    from contracts.api import DASHBOARD_SENDER, HttpEngine, create_app

    app = create_app(stubs.StubEngine(allowlist={"team", DASHBOARD_SENDER}))
    remote = HttpEngine(client=TestClient(app))
    conformance.check_engine(remote, team_sender="team")
    assert remote.set_freeze(True, requested_by=DASHBOARD_SENDER) is True


def test_stub_app_allows_the_dashboard(monkeypatch):
    from fastapi.testclient import TestClient

    from contracts.api import DASHBOARD_SENDER, create_stub_app

    monkeypatch.setenv("TEAM_ALLOWLIST_ADDRESSES", "agent1qexample")
    client = TestClient(create_stub_app())
    assert client.post("/run_round", headers={"X-Requested-By": DASHBOARD_SENDER}).status_code == 200
    assert client.post("/run_round", headers={"X-Requested-By": "stranger"}).status_code == 403


def test_mock_llm_returns_valid_instances_for_every_schema():
    llm = MockLLM()
    assert isinstance(llm, LLMClient)
    models = [obj for obj in vars(schemas).values()
              if isinstance(obj, type) and issubclass(obj, schemas.BaseModel) and obj.__module__ == schemas.__name__]
    for model in models:
        response = llm.complete([Message(role="user", content="hi")], role="critic", response_schema=model)
        assert isinstance(response.parsed, model)


def test_mock_llm_uses_canned_factories():
    canned = {schemas.Vote: lambda _: schemas.Vote(tx_id="t", voter="timing", score=0.7, vote=schemas.Action.BLOCK)}
    response = MockLLM(canned).complete([], role="timing", response_schema=schemas.Vote)
    assert response.parsed.score == 0.7


def test_schema_bounds_are_enforced():
    with pytest.raises(ValidationError):
        schemas.Vote(tx_id="t", voter="timing", score=1.5, vote=schemas.Action.ALLOW)
    with pytest.raises(ValidationError):
        schemas.ShelfState(key=mock_instance(schemas.ShelfKey), attacker_ids=["a", "b", "c", "d"])


def test_shelf_smoothing_matches_spec():
    shelf = schemas.ShelfState(key=mock_instance(schemas.ShelfKey), attempts=8, caught=4)
    assert shelf.catch_rate == pytest.approx(5 / 10)
    assert shelf.learnability == pytest.approx(0.25)


def test_attacker_model_is_from_the_other_family():
    for parent in (*config.GLM_MODELS, *config.DEEPSEEK_MODELS):
        child = config.attacker_model_for(parent)
        assert config.family(child) != config.family(parent)
    assert config.MODEL_ROLES["held_out"] not in {m for r, m in config.MODEL_ROLES.items() if r != "held_out"}


def test_db_sql_defines_every_logged_table():
    sql = DB_SQL.read_text()
    tables = set(re.findall(r"create table if not exists (\w+)", sql))
    expected = {
        "runs", "transactions", "ground_truth", "features", "votes", "decisions", "escalations", "settlements",
        "rulebook_versions", "reflections", "features_added", "attackers", "playbook_waves", "archive_snapshots",
        "archive_events", "defender_actions", "critic_checks", "threat_reports", "mandates", "merchant_pages",
        "shopper_sessions", "round_metrics", "llm_spend", "chat_requests",
    }
    assert expected <= tables


@pytest.mark.skipif(os.environ.get("CONTRACTS_DB_TEST") != "1", reason="set CONTRACTS_DB_TEST=1 to apply db.sql to a throwaway schema")
def test_db_sql_applies_cleanly():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("drop schema if exists contracts_check cascade")
        conn.execute("create schema contracts_check")
        try:
            conn.execute("set search_path to contracts_check")
            conn.execute(DB_SQL.read_text())
        finally:
            conn.execute("drop schema if exists contracts_check cascade")


def test_stub_engine_with_an_empty_allowlist_refuses_everyone():
    from contracts.entrypoints import NotAllowed

    with pytest.raises(NotAllowed):
        stubs.StubEngine(allowlist=set()).run_round(requested_by="stub-team")


def test_mock_llm_reports_usage():
    usage = MockLLM().usage()
    assert {"calls", "input_tokens", "output_tokens", "cost_usd"} <= set(usage)


def test_stub_settlement_says_why_it_did_not_settle():
    tx = conformance.sample_tx()
    allowed = schemas.Decision(tx_id=tx.tx_id, action=schemas.Action.ALLOW, weighted_score=0.0, votes=[])
    blocked = allowed.model_copy(update={"action": schemas.Action.BLOCK})
    assert stubs.StubSettlement().settle(tx, allowed).reason == ""
    assert stubs.StubSettlement().settle(tx, blocked).reason


def test_http_explain_of_an_unknown_tx_is_a_404_and_a_key_error_for_the_client():
    from fastapi.testclient import TestClient

    from contracts.api import HttpEngine, create_app

    class Missing(stubs.StubEngine):
        def explain(self, tx_id):
            raise KeyError(tx_id)

    client = TestClient(create_app(Missing()))
    assert client.get("/explain/nope").status_code == 404
    with pytest.raises(KeyError):
        HttpEngine(client=client).explain("nope")


def test_attack_feedback_says_whether_the_money_moved_and_defaults_to_unknown():
    assert schemas.AttackFeedback(tx_id="t", outcome=schemas.Action.ALLOW).settled is None
    assert schemas.AttackFeedback(tx_id="t", outcome=schemas.Action.STEP_UP, settled=True).settled is True


def test_round_metrics_carry_total_cost_and_default_it_to_zero():
    metrics = schemas.RoundMetrics(run_id="r", seed=1, round=1)
    assert metrics.total_cost_cents == 0
    assert schemas.RoundMetrics(run_id="r", seed=1, round=1, total_cost_cents=1234).total_cost_cents == 1234


def test_spawned_attackers_have_roles_on_the_other_family():
    for lane in ("timing", "graph", "identity", "velocity", "mandate"):
        assert config.family(config.MODEL_ROLES[f"spawn_{lane}"]) != config.family(config.MODEL_ROLES[lane])
    assert config.MODEL_ROLES["held_out"] not in {m for r, m in config.MODEL_ROLES.items() if r != "held_out"}

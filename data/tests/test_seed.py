"""R1 checks: the seed data fills every table, validates against the schemas, and only ever
touches seed-* rows. The database test runs only with SEED_DB_TEST=1 (it writes to a throwaway
schema at DATABASE_URL and drops it again)."""

import os
import re

import pytest

from bench.metrics import compute_round_metrics, load_tables
from contracts import schemas
from data.apply_schema import apply_schema, expected_tables
from data.seed import CLEAR_SQL, FROZEN_ROUNDS, ROUNDS, RUN_ID, TABLE_ORDER, build_seed, clear, insert

PAYLOAD_MODELS = {
    "transactions": ("payload", schemas.Transaction),
    "votes": ("payload", schemas.Vote),
    "decisions": ("payload", schemas.Decision),
    "attackers": ("payload", schemas.AttackerRecord),
    "playbook_waves": ("playbook", schemas.Playbook),
    "archive_snapshots": ("payload", schemas.ShelfState),
    "threat_reports": ("payload", schemas.ThreatReport),
    "mandates": ("payload", schemas.Mandate),
    "merchant_pages": ("payload", schemas.PageScan),
    "round_metrics": ("payload", schemas.RoundMetrics),
}


@pytest.fixture(scope="module")
def tables():
    return build_seed()


def test_seed_fills_every_table_in_db_sql(tables):
    assert set(TABLE_ORDER) == set(expected_tables())
    empty = [t for t in TABLE_ORDER if not tables[t]]
    assert not empty, f"empty tables: {empty}"


def test_payloads_validate_against_the_schemas(tables):
    for table, (column, model) in PAYLOAD_MODELS.items():
        for row in tables[table]:
            model.model_validate(row[column])
    for row in tables["ground_truth"]:
        schemas.GroundTruth.model_validate(row)
    for row in tables["escalations"]:
        assert 1 <= len(row["tool_calls"]) <= 4
        [schemas.ToolCall.model_validate(c) for c in row["tool_calls"]]
    for row in tables["threat_reports"]:
        schemas.ThreatPattern.model_validate(row["pattern"])


def test_every_seed_row_is_marked(tables):
    for name, rows in tables.items():
        for row in rows:
            if "run_id" in row and row["run_id"] is not None:
                assert row["run_id"].startswith("seed-"), name
    assert all(r["mandate_id"].startswith("seed-") for r in tables["mandates"])
    assert all(r["url"].startswith("https://seed-") for r in tables["merchant_pages"])
    assert all(r["report_id"].startswith("seed-") for r in tables["threat_reports"])
    assert all(r["sender"].startswith("seed-") for r in tables["chat_requests"])
    tx_ids = {r["tx_id"] for r in tables["transactions"]}
    for name in ("ground_truth", "features", "votes", "decisions", "escalations", "settlements"):
        assert {r["tx_id"] for r in tables[name]} <= tx_ids, name


def test_clear_only_targets_seed_rows():
    for stmt in CLEAR_SQL:
        assert re.search(r"like 'seed-%'|like 'https://seed-%'", stmt), stmt


def test_shape_of_the_seeded_run(tables):
    rounds = {r["round"] for r in tables["transactions"]}
    assert rounds == set(range(1, ROUNDS + 1))
    for r in rounds:
        assert len([s for s in tables["archive_snapshots"] if s["round"] == r]) == 24
    assert len({s["shelf_id"] for s in tables["archive_snapshots"]}) == 24
    sources = {r["source"] for r in tables["attackers"]}
    assert {"outsider", "spawned", "threat_intel"} <= sources and "held_out" not in sources
    legit = sum(r["source"] == "legit" for r in tables["transactions"])
    assert legit > len(tables["transactions"]) / 2, "legitimate traffic must be the majority"
    assert tables["escalations"], "some payments escalate to the investigator"
    assert all(len([v for v in tables["votes"] if v["tx_id"] == d["tx_id"]]) == 6 for d in tables["decisions"][:50])


def test_frozen_stretch_logs_no_learning(tables):
    for name in ("rulebook_versions", "reflections", "defender_actions"):
        assert not {r["round"] for r in tables[name]} & FROZEN_ROUNDS, name
    frozen = {r["round"] for r in tables["round_metrics"] if r["payload"]["frozen"]}
    assert frozen == FROZEN_ROUNDS


def test_metrics_trend_better_and_climb_while_frozen(tables):
    m = {r: compute_round_metrics(tables, RUN_ID, r) for r in range(1, ROUNDS + 1)}
    assert m[10].total_cost_cents < 0.6 * m[1].total_cost_cents
    assert m[10].pr_auc > m[1].pr_auc and m[10].recall_at_target_fpr > m[1].recall_at_target_fpr
    assert m[10].metrics.escalation_rate < m[2].metrics.escalation_rate
    assert m[10].metrics.archive_coverage > m[2].metrics.archive_coverage
    assert m[7].total_cost_cents > m[5].total_cost_cents, "losses climb during the freeze"
    assert m[10].total_cost_cents < m[7].total_cost_cents, "and recover after it"


def test_logged_round_metrics_match_the_tables(tables):
    for row in tables["round_metrics"]:
        assert compute_round_metrics(tables, RUN_ID, row["round"]).metrics.model_dump(mode="json") == row["payload"]


def test_seed_is_deterministic(tables):
    again = build_seed()
    assert [r["payload"] for r in again["round_metrics"]] == [r["payload"] for r in tables["round_metrics"]]
    assert [r["tx_id"] for r in again["transactions"]] == [r["tx_id"] for r in tables["transactions"]]


@pytest.mark.skipif(os.environ.get("SEED_DB_TEST") != "1", reason="set SEED_DB_TEST=1 to seed a throwaway schema")
def test_seed_round_trips_through_postgres(tables):
    from data.db import connect

    with connect() as conn:
        conn.execute("drop schema if exists seed_check cascade")
        conn.execute("create schema seed_check")
        try:
            conn.execute("set search_path to seed_check")
            assert sorted(apply_schema(conn)) == sorted(expected_tables())
            conn.execute("insert into runs (run_id, seed) values ('not-seed-run', 1)")
            insert(conn, tables)
            loaded = load_tables(conn, RUN_ID)
            for r in range(1, ROUNDS + 1):
                assert (compute_round_metrics(loaded, RUN_ID, r).metrics
                        == compute_round_metrics(tables, RUN_ID, r).metrics)
            clear(conn)
            assert conn.execute("select count(*) from transactions").fetchone()[0] == 0
            assert conn.execute("select run_id from runs").fetchall() == [("not-seed-run",)]
        finally:
            conn.execute("drop schema if exists seed_check cascade")

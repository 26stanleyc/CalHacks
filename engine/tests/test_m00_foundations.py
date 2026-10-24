"""M0 automated checks (spec 8.2): a hand-built transaction through the Bureau produces
transaction, vote and decision rows within 5 s."""

import os
import socket
import time
from pathlib import Path

import pytest

from contracts.schemas import Action, Lane
from engine.bureau import EngineBureau
from engine.smoke import sample_transaction
from engine.store import MemoryStore

pytestmark = pytest.mark.m00

DB_SQL = Path(__file__).resolve().parents[2] / "contracts" / "db.sql"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def memory_bureau():
    store = MemoryStore()
    bureau = EngineBureau(store, port=free_port(), seed_prefix="test-m00")
    bureau.start()
    yield bureau, store
    bureau.stop()


def test_one_transaction_flows_through_every_agent_within_5s(memory_bureau):
    bureau, store = memory_bureau
    tx = sample_transaction("test-run")
    started = time.perf_counter()
    decision = bureau.submit_sync(tx, timeout=5.0)
    assert time.perf_counter() - started <= 5.0
    assert decision.tx_id == tx.tx_id and decision.action == Action.ALLOW
    assert [t.tx_id for t in store.transactions] == [tx.tx_id]
    assert sorted(v.voter for v in store.votes) == sorted(lane.value for lane in bureau.lanes)
    assert [d.tx_id for d in store.decisions] == [tx.tx_id]


def test_several_transactions_are_each_decided_once(memory_bureau):
    bureau, store = memory_bureau
    txs = [sample_transaction("test-run").model_copy(update={"tx_id": f"tx-{i}"}) for i in range(5)]
    decisions = [bureau.submit_sync(tx, timeout=5.0) for tx in txs]
    assert [d.tx_id for d in decisions] == [tx.tx_id for tx in txs]
    assert len(store.votes) == 5 * len(bureau.lanes)
    assert len(store.decisions) == 5


def test_s1_runs_the_four_starting_specialists(memory_bureau):
    bureau, _ = memory_bureau
    assert set(bureau.lanes) == {Lane.TIMING, Lane.GRAPH, Lane.IDENTITY, Lane.VELOCITY}


@pytest.mark.skipif(os.environ.get("ENGINE_DB_TEST") != "1", reason="set ENGINE_DB_TEST=1 to run against a throwaway Postgres schema")
def test_rows_land_in_postgres_within_5s():
    import psycopg

    from engine.store import PostgresStore

    schema = "engine_m00_check"
    admin = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    admin.execute(f"drop schema if exists {schema} cascade")
    admin.execute(f"create schema {schema}")
    try:
        admin.execute(f"set search_path to {schema}")
        admin.execute(DB_SQL.read_text())
        store = PostgresStore(schema=schema)
        store.start_run("test-run", seed=42)
        bureau = EngineBureau(store, port=free_port(), seed_prefix="test-m00-db")
        bureau.start()
        tx = sample_transaction("test-run")
        started = time.perf_counter()
        try:
            bureau.submit_sync(tx, timeout=5.0)
        finally:
            bureau.stop()
            store.close()
        assert time.perf_counter() - started <= 5.0
        counts = {t: admin.execute(f"select count(*) from {t} where tx_id = %s", (tx.tx_id,)).fetchone()[0]
                  for t in ("transactions", "votes", "decisions")}
        assert counts == {"transactions": 1, "votes": 4, "decisions": 1}
    finally:
        admin.execute(f"drop schema if exists {schema} cascade")
        admin.close()

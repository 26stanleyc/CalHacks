"""copy_run: a run benchmarked in memory can be written to Postgres for the dashboard."""

import os
from pathlib import Path

import pytest

from engine.engine import FraudEngine
from engine.sim.marketplace import MarketplaceWorld
from engine.store import ROW_TABLES, MemoryStore, copy_run

DB_SQL = Path(__file__).resolve().parents[2] / "contracts" / "db.sql"


@pytest.mark.skipif(os.environ.get("ENGINE_DB_TEST") != "1", reason="set ENGINE_DB_TEST=1 to run against a throwaway Postgres schema")
def test_a_memory_run_copies_into_postgres_whole():
    import psycopg

    from engine.store import PostgresStore

    memory = MemoryStore()
    engine = FraudEngine(store=memory, world=MarketplaceWorld(seed=42), allowlist={"t"}, run_id="copy-check", tuning_tries=5)
    for _ in range(2):
        engine.run_round(requested_by="t")

    schema = "engine_copy_check"
    admin = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    admin.execute(f"drop schema if exists {schema} cascade")
    admin.execute(f"create schema {schema}")
    try:
        admin.execute(f"set search_path to {schema}")
        admin.execute(DB_SQL.read_text())
        store = PostgresStore(schema=schema)
        copy_run(memory, store)
        store.close()
        count = lambda table: admin.execute(f"select count(*) from {table}").fetchone()[0]
        assert count("runs") == len(memory.runs)
        assert count("transactions") == len(memory.transactions)
        assert count("decisions") == len({d.tx_id for d in memory.decisions})
        assert count("round_metrics") == len(memory.metrics) == 2
        for table in ("attackers", "rulebook_versions", "reflections"):
            assert count(table) == len(memory.rows[table]) > 0, table
        assert set(ROW_TABLES) <= {r[0] for r in admin.execute(
            "select table_name from information_schema.tables where table_schema = %s", (schema,)).fetchall()}
    finally:
        admin.execute(f"drop schema if exists {schema} cascade")
        admin.close()

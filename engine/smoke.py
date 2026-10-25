"""M0 smoke test: one hand-built transaction through the Bureau into the real log tables.

    uv run python -m engine.smoke

Writes one run with run_id "smoke-<timestamp>" (append-only, so it stays in the tables).
"""

import os
import time
from datetime import datetime, timezone

import psycopg

from contracts.config import flag  # loads .env
from contracts.schemas import Mandate, TrafficSource, Transaction
from engine.bureau import EngineBureau
from engine.pipeline import DefensePipeline
from engine.store import PostgresStore


def sample_transaction(run_id: str) -> Transaction:
    mandate = Mandate(mandate_id="smoke-md-1", principal_id="smoke-p-1", per_payment_cap_cents=3_000,
                      daily_cap_cents=10_000, request_text="buy a USB-C charger under $30")
    return Transaction(tx_id=f"{run_id}-tx-1", run_id=run_id, round=1, ts=0.0, agent_id="smoke-agent-1",
                       principal_id="smoke-p-1", merchant_id="smoke-m-1", product_id="smoke-charger",
                       category="electronics", amount_cents=2_499, listed_price_cents=2_499,
                       mandate=mandate, source=TrafficSource.LEGIT)


def count_rows(tx_id: str) -> dict[str, int]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        return {table: conn.execute(f"select count(*) from {table} where tx_id = %s", (tx_id,)).fetchone()[0]
                for table in ("transactions", "votes", "decisions")}


def main() -> int:
    run_id = "smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    store = PostgresStore()
    store.start_run(run_id, seed=42, defense="full", config={"milestone": "M0 smoke", "mock_llm": flag("USE_MOCK_LLM", True)})
    bureau = EngineBureau(DefensePipeline(), store=store)
    bureau.start()
    tx = sample_transaction(run_id)
    started = time.perf_counter()
    try:
        decision = bureau.submit_sync(tx, timeout=5.0)
    finally:
        bureau.stop()
        store.close()
    elapsed = time.perf_counter() - started
    rows = count_rows(tx.tx_id)
    ok = rows == {"transactions": 1, "votes": 4, "decisions": 1} and elapsed <= 5.0
    print(f"run_id={run_id} decision={decision.action.value} elapsed={elapsed:.2f}s rows={rows}")
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Serve the real engine behind the shared HTTP bridge (contracts/api.py), for Ryan's dashboard
and Eric's chat agents.

    uv run python -m engine.serve                  # in-process transport, logs to Supabase
    ENGINE_TRANSPORT=bureau uv run python -m engine.serve

ENGINE_STORE=memory keeps logs in memory instead of Postgres.
"""

import os
from datetime import datetime, timezone

import uvicorn

from contracts.api import create_app, team_allowlist
from contracts.config import flag  # noqa: F401  (loads .env)
from engine.engine import FraudEngine
from engine.store import MemoryStore, PostgresStore


def build_engine() -> FraudEngine:
    store = MemoryStore() if os.environ.get("ENGINE_STORE") == "memory" else PostgresStore()
    run_id = os.environ.get("ENGINE_RUN_ID") or "dev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    engine = FraudEngine(store=store, run_id=run_id, seed=int(os.environ.get("ENGINE_SEED", 42)),
                         allowlist=team_allowlist(), transport=os.environ.get("ENGINE_TRANSPORT", "inprocess"))
    return engine.start()


def main() -> None:
    engine = build_engine()
    try:
        uvicorn.run(create_app(engine), host="127.0.0.1", port=int(os.environ.get("ENGINE_API_PORT", 8000)))
    finally:
        engine.stop()


if __name__ == "__main__":
    main()

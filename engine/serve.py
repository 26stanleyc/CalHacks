"""Serve the real engine behind the shared HTTP bridge (contracts/api.py), for Ryan's dashboard
and Eric's chat agents (ENGINE_SOURCE=http).

    uv run python -m engine.serve                  # in-process transport, logs to Supabase
    ENGINE_TRANSPORT=bureau uv run python -m engine.serve

See engine/entrypoints.py for the other switches (ENGINE_STORE, ENGINE_RUN_ID, ENGINE_SEED,
ENGINE_LIVE_SETTLEMENT).
"""

import os

import uvicorn

from contracts.api import create_app
from engine.entrypoints import build_engine


def main() -> None:
    engine = build_engine()
    try:
        uvicorn.run(create_app(engine), host="127.0.0.1", port=int(os.environ.get("ENGINE_API_PORT", 8000)))
    finally:
        engine.stop()


if __name__ == "__main__":
    main()

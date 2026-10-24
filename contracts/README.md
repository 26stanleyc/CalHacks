# Shared contracts

The agreements between Ryan (data, dashboard, benchmarks), Eric (integrations, Apify) and Stanley (core engine). See spec section 8.4.

| File | What it is |
|---|---|
| `schemas.py` | Data formats: Transaction, Mandate, Vote, Decision, Playbook, AttackerRecord, ShelfState, RoundMetrics, ThreatReport, PageScan, ... |
| `db.sql` | The append-only Postgres tables. Ryan applies it to Supabase. |
| `llm.py` | `LLMClient` (the only way to call a model) and `MockLLM` (free, canned, schema-valid answers) |
| `config.py` | `USE_MOCK_LLM` / `USE_STUB_ENGINE` switches and the model assignment from spec 2.1 |
| `interfaces.py` | Plug shapes: `Voter`, `Attacker`, `PageScanner`, `ThreatIntelSource`, `Settlement`, `Defense` (a benchmark contender: full system, baseline or ablation) |
| `entrypoints.py` | The `Engine` functions chat agents and the dashboard call |
| `api.py` | The HTTP bridge: one route per `Engine` function, plus `HttpEngine`, a client that implements `Engine` over HTTP |
| `stubs.py` | A fake engine and fake plug-ins, so every part runs alone |
| `conformance.py` | Checks your real implementation must pass, e.g. `check_voter(MyVoter())` |

## Run the checks

```bash
uv run pytest
```

To also apply `db.sql` to a throwaway schema in the database from `DATABASE_URL` (created and dropped again):

```bash
CONTRACTS_DB_TEST=1 uv run --with "psycopg[binary]" pytest -k applies
```

## The engine over HTTP (for the dashboard and other processes)

Start the API on the stub engine, so the dashboard works before the real engine exists:

```bash
uv run uvicorn contracts.api:create_stub_app --factory --port 8000
```

| Route | Engine function |
|---|---|
| `POST /check_payment` (body `{tx, mandate}`) | `check_payment` |
| `GET /explain/{tx_id}` | `explain` |
| `GET /scan_page?url=...` | `scan_page` |
| `GET /metrics?run_id=&round=` | `get_metrics` |
| `POST /run_round` (header `X-Requested-By`) | `run_round`, team only |
| `POST /freeze` (body `{on}`, header `X-Requested-By`) | `set_freeze`, team only |
| `GET /threat_intel?limit=` | `latest_threat_intel` |

- Interactive docs are at `http://localhost:8000/docs`.
- The dashboard sends `X-Requested-By: dashboard`, which is always allowed. Chat senders must be in `TEAM_ALLOWLIST_ADDRESSES`. Run it on localhost only: the header is trusted, not verified.
- From Python, `HttpEngine("http://localhost:8000")` behaves exactly like an in-process `Engine`. Stanley's real engine will serve the same routes via `create_app(engine)`.

## Changing a contract

After kickoff review these are frozen. A change needs a heads-up to the other two, plus matching updates to `stubs.py` and the tests in the same commit.

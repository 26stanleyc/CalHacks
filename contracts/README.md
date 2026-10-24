# Shared contracts

The agreements between Ryan (data, dashboard, benchmarks), Eric (integrations, Apify) and Stanley (core engine). See spec section 8.4.

| File | What it is |
|---|---|
| `schemas.py` | Data formats: Transaction, Mandate, Vote, Decision, Playbook, AttackerRecord, ShelfState, RoundMetrics, ThreatReport, PageScan, ... |
| `db.sql` | The append-only Postgres tables. Ryan applies it to Supabase. |
| `llm.py` | `LLMClient` (the only way to call a model) and `MockLLM` (free, canned, schema-valid answers) |
| `config.py` | `USE_MOCK_LLM` / `USE_STUB_ENGINE` switches and the model assignment from spec 2.1 |
| `interfaces.py` | Plug shapes: `Voter`, `Attacker`, `PageScanner`, `ThreatIntelSource`, `Settlement` |
| `entrypoints.py` | The `Engine` functions chat agents and the dashboard call |
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

## Changing a contract

After kickoff review these are frozen. A change needs a heads-up to the other two, plus matching updates to `stubs.py` and the tests in the same commit.

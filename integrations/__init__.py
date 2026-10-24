"""Eric's track (P2): integrations and Apify.

Everything here sits behind a contract in `contracts/`, so the engine, dashboard and
benchmarks only ever see the shared shapes:

| Module                    | Contract                              |
|---------------------------|---------------------------------------|
| `bitdeer.py`              | `contracts.llm.LLMClient`             |
| `x402_settlement.py`      | `contracts.interfaces.Settlement`     |
| `page_scanner.py`         | `contracts.interfaces.PageScanner`    |
| `asi_one/`                | `contracts.entrypoints.Engine` caller |

Nothing in here imports from `engine/`, `data/`, `bench/` or `dashboard/`.
"""

INTEGRATIONS_VERSION = "0.1.0"

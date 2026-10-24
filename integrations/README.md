# integrations/ (Eric, P2)

Everything that talks to something outside the repo. Each piece sits behind a contract in
`contracts/`, so the engine, dashboard and benchmarks only ever see the shared shapes.

| Module | Contract | State |
|---|---|---|
| `bitdeer.py` | `contracts.llm.LLMClient` | **real**, credits live; falls back to `MockLLM` |
| `x402_settlement.py` | `contracts.interfaces.Settlement` | **real** on Base Sepolia; stub mode for precompute |
| `page_scanner.py` | `contracts.interfaces.PageScanner` | detector real, Apify fetch ready; wired up at M10 |
| `asi_one/` | calls `contracts.entrypoints.Engine` | **real** agents on Agentverse, on the stub engine until I2 |

```bash
uv run pytest                                  # includes integrations/tests
uv run python scripts/probe_integrations.py    # is each outside service really there?
```

## The agents

```bash
uv run python -m integrations.asi_one.hello_agent        # M0 smoke test
uv run python -m integrations.asi_one.fraud_desk         # check / explain / metrics / intel
uv run python -m integrations.asi_one.merchant_scanner   # scan_page
```

Each one registers its own Agentverse mailbox on startup from `AGENTVERSE_API_KEY`, so
there is nothing to click. Addresses are fixed by the seeds in `.env`.

## Switches

All optional; the defaults are what runs today.

| Variable | Default | Does |
|---|---|---|
| `ENGINE_SOURCE` | `stub` | `stub`, `http` or `inprocess`. **The I2 switch.** |
| `ENGINE_HTTP_URL` | `http://localhost:8000` | where `http` points |
| `USE_MOCK_LLM` | unset | `1` forces `MockLLM` even with credits |
| `X402_MODE` | `real` if the wallets are set | `stub` for precompute, `real` for the demo |
| `X402_MAX_SETTLE_CENTS` | `100` | caps one real settlement; real mode only |
| `TEAM_ALLOWLIST_ADDRESSES` | empty | senders allowed to run rounds or freeze from chat |
| `BITDEER_REASONING_HEADROOM` | `4000` | tokens left for hidden reasoning on top of `max_tokens` |

## What is enforced here, not in the engine (spec 3.7)

- a payment submitted through chat is always `source=asi_one`, set in
  `asi_one/tools.py` and never taken from the model
- `run_round` and `set_freeze` raise `NotAllowed` outside `TEAM_ALLOWLIST_ADDRESSES`
- a blocked or stepped-up payment never settles, and makes no network call

## Reading the layout

`asi_one/` is four small pieces so each can be tested without a model or a network:

- `tools.py` - one tool per `Engine` entry point, the spec 3.7 rules, and the answer formatting
- `router.py` - message -> tool choice (by LLM, or by keyword when there is no model)
- `agent_base.py` - uAgents Chat Protocol, mailbox registration, the two platform workarounds
- `engine_source.py` - which `Engine` to call

The answers are formatted in code rather than by a second LLM call, so the numbers and rule
ids in a reply are always the engine's own, the tests need no model, and a reply costs one
call instead of two. Routing is the only thing the model does.

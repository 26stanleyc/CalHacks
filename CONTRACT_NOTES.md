# Contract notes

Things Eric (P2, integrations) ran into while building against `contracts/`. Per spec 8.4
rule 1 the contracts are frozen, so nothing here was changed: this is the list to discuss
and, where we agree, for Stanley to apply with matching `stubs.py` and test updates.

Nothing here blocks anyone. Each item says what I did instead.

---

## 1. `SettlementResult` cannot say why a payment did not settle

`SettlementResult` is `{tx_id, settled, tx_hash, network}`. When a settlement is refused,
the reason matters: over the mandate cap, facilitator rejected the authorization, buyer
wallet out of test USDC, or simply "the decision was block". Right now all of those are
`settled=False` and indistinguishable to the dashboard and to `db.sql`.

**Suggested:** add `reason: str = ""` (and maybe `refused_by: str | None`). Default-empty,
so nothing existing breaks.

**Doing instead:** `integrations.x402_settlement` keeps a `SettlementAttempt` per call with
the reason and a hint, reachable via `settlement.attempts` and `settlement.summary()`. It
is in the process logs but not in the returned contract object.

## 2. `contracts.api.team_allowlist()` admits a sender called `dashboard`

It returns `TEAM_ALLOWLIST_ADDRESSES | {"dashboard"}`. That is right for the local HTTP
bridge, where the dashboard identifies itself with that header and the port is localhost.
It is wrong for a public chat agent: a chat sender named `dashboard` would get the round
controls. Not exploitable today, because Agentverse senders are always `agent1q...`
addresses, but it is the kind of thing that stops being true later.

**Suggested:** either rename it `http_team_allowlist()`, or split the dashboard sentinel out
so the chat path and the HTTP path cannot drift.

**Doing instead:** `integrations.settings.chat_allowlist()` reads `TEAM_ALLOWLIST_ADDRESSES`
only, and the chat agents use that. (It is currently empty - see item 8.)

## 3. `LLMClient.max_tokens` means different things on a reasoning model

Every Bitdeer chat model in spec 2.1 is a reasoning model: it spends `completion_tokens`
on hidden `reasoning_content` *before* the answer. With the contract's default
`max_tokens=1024`, a real call often comes back with `content=""` and
`finish_reason="length"` - the model thought for the whole budget and never answered. In
testing, a single structured-output call used 628 reasoning tokens before writing 15
tokens of JSON.

**Suggested:** document `max_tokens` in `contracts/llm.py` as the *answer* budget, which is
what callers mean, and leave reasoning headroom to the client.

**Doing instead:** `BitdeerLLM` treats it as the answer budget and adds headroom
(`BITDEER_REASONING_HEADROOM`, default 4000), then retries once with double if the answer
still came back empty and truncated. Callers can keep passing 1024.

## 4. `LLMClient` has no spend accessor, but spend has to be tracked

Spec 4.1 needs per-round and per-run spend, and `RoundMetrics.llm_spend_usd` expects a
number. `LLMResponse` carries `cost_usd` per call, but the `LLMClient` protocol has no way
to ask a client for its running total, so anything typed against the protocol has to sum
responses itself and cannot see a wrapper's totals.

**Suggested:** add `def usage(self) -> dict: ...` to the protocol (or a small `Usage` model).

**Doing instead:** `BitdeerLLM.usage()` and `FallbackLLM.usage()` both exist and return
calls, tokens, cost, `by_model`, `by_role` and `unpriced_models`. They are just not part of
the protocol, so you have to know you have a Bitdeer client to call them.

## 5. `StubEngine` turns an empty allowlist into a non-empty one

`contracts/stubs.py`: `self.allowlist = allowlist or {"stub-team"}`. Passing `set()` -
"nobody is on the team yet", which is our actual state - silently becomes `{"stub-team"}`.
It did not bite us, because the chat layer checks the sender before calling the engine, but
`StubEngine(allowlist=set()).run_round(requested_by="stub-team")` succeeding is surprising.

**Suggested:** `allowlist if allowlist is not None else {"stub-team"}`.

**Doing instead:** nothing needed; the chat agents refuse first and are tested for it
(`test_an_empty_allowlist_refuses_everyone`).

## 6. uAgents needs two lines of setup that are not obvious, and they affect the Bureau too

Both of these cost real debugging time, and Stanley's Bureau will hit both:

- **Python 3.14.** `Agent.__init__` calls `asyncio.get_event_loop()`. Up to 3.13 that
  created a loop on demand; on 3.14 it raises `RuntimeError: There is no current event
  loop`, so constructing *any* `Agent` at import time fails. Our venv is 3.14
  (`requires-python = ">=3.12"`).
- **TLS on macOS.** uAgents talks to Agentverse over `aiohttp`, which uses the system CA
  store. python.org builds of Python on macOS ship without one, so every Agentverse call
  fails with `CERTIFICATE_VERIFY_FAILED` and the mailbox never connects. `httpx` is fine
  because it bundles certifi, which is why Bitdeer and x402 worked and only the agents did
  not. `scripts/fund_wallet.py` already had this workaround for the CDP SDK.

**Doing instead:** `integrations.asi_one.agent_base.ensure_event_loop()` and
`integrations.settings.ensure_ca_bundle()`. Both are importable and safe to call twice -
please reuse them rather than rediscovering this.

## 7. `mailbox=True` alone does not make an agent reachable

Constructing `Agent(mailbox=True)` logs `Agent mailbox not found: create one using the
agent inspector`, and anyone who replies to that agent gets
`404: Target agent not found`. The mailbox has to be registered against the Agentverse
account, either by clicking Connect in the local inspector or with the API key.

**Doing instead:** `agent_base.connect_mailbox()` does it on startup from
`AGENTVERSE_API_KEY`, so there is nothing to click. Worth knowing if you write a fourth
agent - I lost a test run to exactly this.

## 8. `TEAM_ALLOWLIST_ADDRESSES` is empty, so nobody can run a round from chat

In `.env` the line is only a comment, so the value is `""`. That is fine and safe for now
(the chat agents refuse everyone), but before the demo the team's own ASI:One sender
addresses need to go in, or "run a round" cannot be demonstrated from chat.

Our two public agent addresses, if you want them on the list:

```
fraud-desk        agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7
merchant-scanner  agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx
```

Those are the agents, not the humans. The allowlist wants whatever sender address ASI:One
presents for each of us, which we can read off the Fraud Desk's log the first time we chat
to it.

## 9. `Mandate.daily_cap_cents` is required, but chat users give one cap

Someone types "mandate under $30". That is a per-payment cap; `Mandate` also requires
`daily_cap_cents`.

**Doing instead:** the chat layer sets `daily_cap_cents` to the per-payment cap when none is
given, rather than inventing a multiple. Flagging it because it means a chat-submitted
payment can look like it breaches a daily cap it was never actually given. If a default
multiple is preferable, say so and I will change it on my side.

## 10. `check_settlement` only pins the negative case

`contracts.conformance.check_settlement` asserts that a blocked payment does not settle,
which is the important half. It never asserts that an *allowed* payment does settle, so an
implementation that refuses everything passes.

**Suggested:** nothing, probably - a real implementation can legitimately fail to settle
(no funds, facilitator down), so the check cannot require success. Noting it so nobody
reads a passing `check_settlement` as "settlement works".

**Doing instead:** `integrations/tests/test_settlement.py` covers the positive case in stub
mode, and the real path is verified by hand against Base Sepolia (see
`evidence/m0/x402-settlement.md`).

---

## Changes I made outside `integrations/`

Per the folder rules, for the record:

- `pyproject.toml` - added dependencies with `uv add` (`uagents`, `x402[evm,httpx]`,
  `eth-account`, `certifi`, `cdp-sdk`) and added `integrations/tests` to
  `[tool.pytest.ini_options] testpaths` so `uv run pytest` picks my tests up. Ryan and
  Stanley will want their own test directories added the same way.
  `cdp-sdk`, `eth-account` and `certifi` also repair `scripts/make_local_wallet.py` and
  `scripts/fund_wallet.py`, which import `cdp`, `eth_account` and `certifi` but were not in
  `pyproject.toml`, so `uv sync` pruned them out of the venv.
- `.gitignore` - added `uagents_core.log`. The uAgents library writes that file into the
  repo root whenever an agent runs, so without the line it turns up in everyone's
  `git status` the moment they run an agent or a Bureau.
- `scripts/probe_integrations.py` - new (mine, per 8.4).
- `evidence/m0/` - new (mine).

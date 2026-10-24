# Contract notes

Gaps and questions found while building against `contracts/`. Each item is a proposal for the
team; each says what its author did instead in the meantime.

**Status (Stanley, 2026-10-24, merging `eric/integrations` and `ryan/data-dashboard`):** applied
in `contracts/` with stub and test updates: Eric 1 (`SettlementResult.reason`, plus a `reason`
column on `settlements`), 3 (`max_tokens` documented as the answer budget), 4 (`usage()` on the
`LLMClient` protocol) and 5 (`StubEngine` keeps an empty allowlist empty). Done at I1: Ryan 8
(everything uses the engine's features) and 9 (the static baseline is the seed rulebooks). The rest
are still open. Stanley's own items, and every edit he made in Ryan's and Eric's folders, are at
the bottom.

## From Eric (P2, integrations)

Per spec 8.4 rule 1 the contracts are frozen, so Eric changed nothing in `contracts/`.


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

## From Ryan (R1–R5), 2026-10-24

1. **`RoundMetrics` has no field for recall at 1% FPR, PR-AUC or total cost.** Spec 5.1 lists all three as per-defense, per-round metrics.
   - Proposal: add `recall_at_1pct_fpr: float | None`, `pr_auc: float | None` and `total_cost_cents: int = 0` to `RoundMetrics`.
   - For now: `bench/metrics.py` returns them next to `RoundMetrics` in `BenchMetrics`.

2. **No marker for exploitability-exam traffic.** Step 13 of the learning loop runs a frozen exam, but neither `Transaction` nor `db.sql` says which logged rows are exam traffic. They must stay out of the round's loss, FPR and learning labels.
   - Proposal: log exam transactions with `wave = 3` (one past the 3 main waves), or add an `exam boolean` column to `transactions`.
   - For now: `bench/config.py` defines `EXAM_WAVE = 3`. Metrics count waves 0–2 as main traffic and wave 3 as the exam. The seed data follows this, and the dashboard labels those rows "exam".

3. **The freeze state isn't logged anywhere in `db.sql`.** M5 says "the freeze button changes the freeze flag in the database", but there is no such flag.
   - Proposal: the engine writes `RoundMetrics.frozen` for each round, which the dashboard already reads. It could also log freeze and unfreeze events, e.g. a `freeze_events (run_id, round, on, requested_by, at)` table.
   - For now: the dashboard reads the live state from `GET /metrics` (`frozen`), and the curves shade rounds whose logged `round_metrics.payload.frozen` is true.

4. **No read-only route for the freeze state.** `POST /freeze` returns the new state, but the only way to read it is `GET /metrics`.
   - That works and is what the dashboard uses. If the real engine's `get_metrics()` is expensive, a `GET /freeze` would help.

5. **The stub API has no CORS.** The browser can't call `:8000` directly.
   - Not a problem: the dashboard calls the engine from Next.js server actions, server to server, so no change is needed.

6. **Where "uncovered" misses come from.** `RoundMetrics.uncovered_misses` has no source table.
   - For now: metrics count `critic_checks` rows with `kind = 'miss_assignment'` whose `critic->>'lane'` is missing or `'uncovered'`.
   - Stanley: please log uncovered misses that way, or tell me where they'll live.

7. **First-contact catch rate scope.** Spec 5.1 defines it for threat-intel attackers.
   - For now: metrics use attackers with `source = threat_intel` whose first main-traffic payment of the run falls in that round. It could be widened to every new attacker if you want.

8. **Placeholder features.** `data/features.py` builds placeholder feature vectors (field × window × aggregate names) so the XGBoost voter and the baselines can run before I1.
   - At I1, the voter and the baselines should read the engine's `features` table, or a `FeatureService.features_for(tx)` the engine exposes.
   - The voter needs no code change: it trains on whatever keys it is given.

9. **The static-rules baseline uses placeholder rules.**
   - `bench/baselines.py::PLACEHOLDER_RULES` stands in until Stanley's seed rulebooks exist. Swap them in so the baseline is "the seed rulebooks, never updated" (spec 5.1).
   - The placeholder rules flag about 8% of legit traffic on seed 42, mostly the subscription-burst persona.

10. **Seed rows in tables without `run_id`.** `data/seed.py --clear` deletes only rows marked as seed rows:
    - `run_id like 'seed-%'`
    - `mandate_id`, `report_id` and chat `sender` starting with `seed-`
    - merchant page URLs starting with `https://seed-`

    Please don't use those prefixes for real rows.

11. **Mandate ids from the population.** `data.shoppers.Population(seed, id_prefix=...)` builds principal, agent and mandate ids.
    - With the default empty prefix, ids look like `p-0001`, `agent-0001` and `md-0001`. Two runs on the same seed then reuse the same `mandate_id`, which is the primary key of `mandates`.
    - Insert mandates with `on conflict do nothing`, or pass a run-specific prefix.

## From Stanley (P3, engine), 2026-10-24: merging both branches (I1, I2 seams)

### Questions for the team

1. **Should a blocked legit mandate breach count as a false positive?** Ryan's shoppers go over
   their per-payment cap on purpose about 1% of the time (`GroundTruth.breaches_mandate`). Spec 3.3
   says a clear mandate breach is hard-vetoed, so the engine blocks them. Both the engine's
   `RoundMetrics.false_positive_rate` and `bench/metrics.py` count those blocks as false
   positives. Proposal: leave them out of the false-positive rate and report them separately. The
   human approval step for them (spec 3.4 point 6: "your agent tried to buy X; approve?") lands
   with mandates in S7.
2. **Daily caps reset at midnight (UTC), not over a rolling 24 hours.** That matches Ryan's
   placeholder features and his one-day rounds. Ryan's honest shoppers now stop buying when the
   next payment would go over their human's daily cap (see below), so the only legit breaches are
   the deliberate per-payment ones.
3. **Ryan 11 (mandate id collisions) can't bite the engine yet:** the engine never writes the
   `mandates` table, because mandates travel inside each transaction's payload.

### Setup gotchas

- **XGBoost on macOS needs OpenMP:** `brew install libomp`, or `import xgboost` fails and
  `bench/tests` can't even be collected.
- **Worktrees have no `.env`.** Python finds the main checkout's `.env` by searching upward, but
  the dashboard reads `../.env` from `dashboard/`. In a worktree, link it:
  `ln -s ../../.env .env` (it stays gitignored).

### Edits Stanley made in Ryan's and Eric's folders

Each has a test. Please review and push back on anything you'd do differently.

**Ryan (`data/`, `bench/`, `dashboard/`)**
- `data/features.py`: now a thin wrapper over the engine's `engine.features.build_features`
  (note 8), so seeded rows, benchmark inputs and real rounds use the same feature names. The
  placeholder builder is gone. `data/tests/test_m01_marketplace.py` checks the same persona
  quirks under the engine names (`t_count_agent_600s`, `v_amount_cents`, `g_principals_per_agent`,
  `g_agent_new`).
- `data/shoppers.py`: honest agents stay inside their human's daily cap. Before, ordinary shoppers
  went over it about 2% of the time without being labeled a breach, and the engine's daily-cap veto
  blocked them. Payments over the per-payment cap are unchanged. A persona that can't fill its
  quota within its caps now raises an error instead of looping forever.
- `bench/baselines.py`: `StaticRulesDefense` is the engine's seed rulebooks, hard vetoes and cost
  matrix, never updated (note 9). `PLACEHOLDER_RULES` and `Rule` are gone. On Ryan's legit traffic
  it flags under 2%, down from about 8%.
- `bench/config.py`, `bench/metrics.py`, `data/seed.py`: one cost matrix. `config.COSTS` is the
  engine's `CostMatrix`, so the full system is scored on the costs it optimizes. Before, the
  benchmark used different numbers (a $2 false-block fixed cost vs $3, $0.50 friction vs $1.50,
  10% abandonment vs 25%), and an abandoned step-up didn't count the goodwill cost.
- `dashboard/src/lib/queries.ts`: the injection flag reads the engine's `b_memo_injection`
  feature. `dashboard/README.md`: start the real engine with `uv run python -m engine.serve`.
- `pyproject.toml`: both branches' dependencies, test paths and markers combined.

**Eric (`integrations/`)**
- `asi_one/tools.py`: explaining an unknown tx id says "I have no record of transaction X"
  instead of failing; a page Apify couldn't read says so instead of looking safe.
- `asi_one/router.py`: the tx id pattern also matches the engine's real round ids
  (`<run>-r<round>-...`).
- `x402_settlement.py`: passes the new `SettlementResult.reason` through.
- `tests/test_asi_one.py`: the in-process test uses a memory store, so it no longer writes to
  Supabase.

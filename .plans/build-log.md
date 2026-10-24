# Build Log and Demo Notes

A running record of what we built at each step, and how each piece shows up in the 3-minute demo. Add an entry to section 5 whenever a milestone lands (template at the bottom), and update the cheat sheet in section 1 when a talking point becomes true.

Plans: [plain-english-overview.md](plain-english-overview.md) · [technical spec](self-red-teaming-fraud-defense-spec.md) · demo script: spec §7, overview §12

**Status key:** ✅ true and provable today · 🟡 partly true, so word it carefully · 🔜 true once the named milestone lands

---

## 1. Demo cheat sheet: what to say, ranked

The lines we most need judges to hear. **Say** is the sentence, **Show** is what's on screen while we say it.

### 1. "Our agents are Fetch.ai uAgents that talk to each other" ✅ (S1)
- **Say:** "Every defender is its own uAgent. When a payment arrives, the orchestrator agent sends it to four specialist agents, each one votes back by message, and the orchestrator makes the call."
- **Show:** `uv run python -m engine.trace` in a big terminal. It prints the 6 agents with their `agent1q...` addresses, then one card tester's payment hopping between them: `SubmitTx` → 4× `ScoreRequest` → 4× `VoteReply` → `DecisionReply`, each hop timed. Timing and velocity vote BLOCK, graph and identity vote ALLOW, the orchestrator blocks, and the whole thing takes about 1 ms. `--scenario legit` shows an honest payment for contrast: all four allow.
- **True today:** 6 agents in one Bureau (ingress, orchestrator, timing, graph, identity and velocity specialists). A test proves the agent path gives exactly the same decisions as calling the code directly. The agents are locked down: each one drops messages from anyone but the expected agent, and the Bureau listens on localhost only.
- **Line to add while the trace runs:** "Each specialist only sees its own facts: timing sees 6, identity sees 2. They can't copy each other, and they disagree here: two say block, two say allow."

### 2. "Every agent's brain is an open model on Bitdeer" 🟡
- **Say (once S4 lands):** "Each defender is a uAgent with its own open-weight model on Bitdeer as its brain: GLM and DeepSeek for defense and attack, with Qwen kept back for the final exam. Between rounds the model rewrites its agent's rulebook. During a round the rulebook runs as code, so a payment is checked in milliseconds. AI writes the rules, code applies them, and AI steps back in for the hard cases."
- **Show:** the model-per-role table (spec §2.1), then a specialist's rulebook before and after a round of reflection.
- **True today:** the per-role model assignment is in `integrations/bitdeer.py`, and the ASI:One chat agents are built to run on `GLM-5.3-Flash` to understand requests. Eric's probe at 01:45 got a live answer from all 5 Bitdeer chat models (`evidence/m0/model-prices.md`). **But** Stanley found the credits not live afterwards, so the overnight build runs on the mock. Re-run `uv run python scripts/probe_integrations.py` before claiming Bitdeer is live.
- **Careful:** right now the specialists vote with hand-written starter rules and don't call Bitdeer yet. Their Bitdeer brains arrive with the critic and reflection (S4), the investigator (S5) and the attackers (S3, S6). Until S4 is merged, say "the agents talk to each other" and "the chat agents run on Bitdeer", not "the specialists think with Bitdeer".
- **Bonus line:** "Each self-made attacker runs on a different model family from the defender that built it, so it doesn't share its parent's blind spots."

### 3. "The defense attacks itself" 🔜 (S6)
- **Say:** "Every defender builds the attacker most likely to beat it. The attackers live in an arena with one shelf per type of attack, so they can't all copy one trick, and training goes wherever the defense is shakiest."
- **Show:** the arena grid going red → green, with arrows showing where each defender's attacker is aiming. Then the gap attacker slipping through, then getting caught.
- **This is the main pitch.** Mention Rainbow Teaming (NeurIPS 2024) as the research it builds on.

### 4. "We sit at the x402 checkpoint, and these are real payments" ✅ (E4)
- **Say:** "Every x402 payment goes through a facilitator before it settles. We're the fraud check inside it, like Stripe Radar for agent payments. A blocked payment never settles."
- **Show:** a live Base Sepolia settlement, then its hash opening on basescan.
- **True today:** 3 real testnet settlements (hashes in `evidence/m0/x402-settlement.md`). A blocked or stepped-up payment makes no network call at all. The facilitator pays the gas. Each real settlement is capped (`X402_MAX_SETTLE_CENTS`).

### 5. "Anyone can use it by chatting in ASI:One" 🟡 (I2 merged; needs a live ASI:One run on the real engine)
- **Say:** "Judges can talk to our Fraud Desk and Merchant Scanner agents in ASI:One. They're a front door to the same engine the dashboard uses, so chat and dashboard always agree."
- **Show:** ASI:One with the dashboard beside it.
- **True today:** both agents are registered on Agentverse with the Chat Protocol and a mailbox, and answered 5 of 5 scripted prompts over the real mailbox. Since the I2 merge they can run on the real engine (`ENGINE_SOURCE=inprocess`, or `http` against `engine.serve`). Not yet re-run over the real mailbox against the real engine.
- **Safety, enforced in code, worth one sentence:** chat-checked payments never train the defense, only our team can run rounds or freeze, and chat text reaches the model only inside a marked data block.

### 6. "It protects both sides: bad buyers and bad stores" 🟡 (M10 / E6)
- **Say:** "A dishonest store can hide 'ignore your budget' in a product page and hijack an honest shopping agent. We scan the page and block the payment because it's not what the human asked for."
- **Show:** the Merchant Scanner flagging the poisoned page, then the Fraud Desk blocking the purchase for breaking the mandate.
- **True today:** the injection detector scores the poisoned test page 1.0 and the clean one 0.0, and still catches reworded and disguised injections (S2 tests). Real Apify page scraping lands at M10.

### 7. "It's fast and doesn't block honest shoppers" ✅ (S2)
- **Say:** "The hot path is plain code: 95% of payments are checked in under 50 ms, with at most 2% of honest shoppers flagged."
- **Show:** the transaction stream, plus the false-alarm curve staying flat.

### 8. "It proves it works" 🔜 (R5, R7)
- **Say:** "XGBoost is strong on fixed data, but once attackers adapt it falls apart. Ours stays hard to beat."
- **Show:** the benchmark chart: us vs fixed rules vs XGBoost vs us without self-made attackers.

### 9. "It's ready before the attack" 🔜 (M11, bonus)
- Only if M11 ships: a news report becomes a watch rule and new attackers, and the scam is caught on first contact. Show the watchlist on vs off.

### Don't claim
- **CRS:** on hold; we have no key (`evidence/m0/crs-decision.md`). Identities are simulated, and we're not entering the CRS track.
- **Spend numbers:** Bitdeer's API doesn't expose prices, so spend shows $0 until someone copies them from the console.
- **Anything on the 🔜 list** until its milestone is merged and rehearsed.

---

## 2. Sponsor checklist

| Sponsor | One-line pitch | Proof on screen | Status |
|---|---|---|---|
| **Fetch.ai** (uAgents, Agentverse, ASI:One) | Every defender, attacker and shopper is a uAgent; two public agents make the whole system usable from ASI:One | Message trace; live ASI:One chat; Agentverse profile links | 🟡 agents talk (S1) and are registered (E2); real engine behind chat at I2 |
| **Bitdeer** | Every AI in the system is an open-weight model on Bitdeer, a different model per role | Model-per-role table; a rulebook rewritten by its model | 🟡 all 5 answered at E3, credits now in doubt; agents get their brains at S4–S6 |
| **Coinbase x402** | Fraud check inside the facilitator, before settlement | Live Base Sepolia tx on basescan | ✅ (E4) |
| **Apify** | Scans merchant pages for hidden instructions (core); scrapes news of new scams (bonus) | Merchant Scanner flags the poisoned page | 🟡 detector real; Apify fetch at M10 |

---

## 3. Numbers to quote

Only numbers we can back up. Add a source for each.

| Number | What it means | Source |
|---|---|---|
| 5 / 5 | Bitdeer chat models that answered a live call (Eric's probe; re-check, credits may not be live) | `evidence/m0/model-prices.md` |
| 10 / ~1 ms | Messages between 6 uAgents to decide one payment, end to end | `uv run python -m engine.trace` |
| 3 | Real x402 settlements on Base Sepolia | `evidence/m0/x402-settlement.md` |
| 5 / 5 | Scripted chat prompts answered over the real Agentverse mailbox | commit 390d9d2 (E1–E4) |
| 1.0 vs 0.0 | Injection score, poisoned vs clean product page | `integrations/tests/test_page_scanner.py` |
| ≤ 50 ms | p95 time to check one payment | `engine/tests/test_m02_defense.py` |
| ≤ 2% | Honest shoppers flagged | `engine/tests/test_m02_defense.py` |
| ≥ 70% | Scripted fraud caught by the starter rules alone, before any learning | `engine/tests/test_m02_defense.py` |
| 152 | Tests passing after E1–E4 | commit 390d9d2 |
| _tbd_ | Loss drop over 10 rounds (target: ≤ 60% of round 1) | M4 / S4 |
| _tbd_ | Exploitability vs baselines | R5 / R7 |

---

## 4. Open items before the demo

- [x] **A visible trace of the agents messaging each other.** `uv run python -m engine.trace`. It's on branch `stanley/uagent-trace` until merged.
- [ ] **Re-check Bitdeer credits** with the probe. Talking point 2 and the Bitdeer sponsor row depend on it.
- [ ] **Add our own ASI:One sender addresses to `TEAM_ALLOWLIST_ADDRESSES`** in `.env`. Without them, "freeze learning" from chat is refused for everyone, including us, which kills a live beat (CONTRACT_NOTES item 8).
- [ ] **ASI:One screenshot** of the hello agent → `evidence/m0/asi-one-hello.png` (manual-checks §1).
- [ ] **Recording** of the 3 Fraud Desk prompts → `evidence/m08/` (manual-checks §2).
- [ ] **Bitdeer prices** from the console → `integrations/model_prices.py`, so the spend panel is real.
- [ ] **Check the Cal Hacks hackpack** on custom frontends. If it rules them out, chat carries the story and the dashboard is supporting visuals (spec §7).
- [ ] **Backup recording** of a full run.

---

## 5. Log

Newest at the bottom. Times are Pacific, from commit timestamps.

### Fri Oct 23, 19:39–23:21 · Planning · Stanley
**Did:**
- Wrote the technical spec and the plain-English overview.
- Picked GLM-5.3 as the critic (the strongest open model) and added the Apify threat-intel watchlist.
- Designed the Tag-Team Arena: each defender builds its own attacker, plus a Rainbow-Teaming-style arena of 24 shelves.
- Merged the separate persona swarm into the arena as outsiders, with one spot on every shelf saved for them.
- Added the investigator, the XGBoost voter, waves and defender choices, plus a scope cut and a budget.
- Fitted the model plan to Bitdeer's real catalog: 5 chat models from 3 families, with Qwen kept only for the held-out exam.
- Split the build 25/25/50 across Ryan, Eric and Stanley, with shared contracts and 3 checkpoints where the parts join.

**Demo angle:** the research credibility. Rainbow Teaming (NeurIPS 2024) and GEPA (ICLR 2026), and why we deliberately left out a shared "hive mind" (MemCollab 2026). One sentence in the pitch, more if judges ask.

### Fri Oct 23, 23:26–23:28 · Scaffold · Stanley, Eric
**Did:**
- Shared Python project: uv, pyproject and an Apify runner.
- Eric added x402 testnet wallet scripts: make a throwaway Base Sepolia wallet and fund it from the CDP faucet.
- Secrets stay out of git.

**Demo angle:** none. Behind the scenes.

### Fri Oct 23, 23:30 → Sat Oct 24, 01:02 · S0 Contracts · Stanley (PRs #1, #2)
**Did:**
- The shared contracts everyone builds against: data shapes, database tables, the AI-model interface with a mock, plug-in interfaces, function names, stand-ins and conformance tests.
- Then an HTTP bridge for the dashboard and a `Defense` interface for the benchmarks.

**Why it matters:** each person builds their part alone against stand-ins, then the parts snap together.

**Demo angle:** if asked how 3 people built this in 36 hours: "contracts first, then three parts built in parallel against stand-ins".

### Sat Oct 24, 01:06 · Decision · Stanley
**Did:** made M10 (malicious stores and page scans) a must-ship and M11 (threat-intel watchlist) a bonus.

**Demo angle:** the "protects both sides" story is the one we commit to. "Ready before the attack" only goes in if M11 ships.

### Sat Oct 24, 01:21 · S1 Engine foundations · Stanley
**Did:**
- A uAgents Bureau with 6 agents: ingress, orchestrator, and 4 specialists (timing, graph, identity, velocity).
- A Postgres log store.
- An M0 smoke test that sends one payment through every agent into the database.

**Proof:** `engine/tests/test_m00_foundations.py` checks that one payment passes through every agent in under 5 s, and the rows land in Postgres. Run it with `uv run python -m engine.smoke`.

**Demo angle:** ⭐ **talking point 1.** This is the "agents talk to each other" moment.

### Sat Oct 24, 01:45 · E1–E4 Integrations · Eric
**Did:**
- **E1, probes:** `scripts/probe_integrations.py` reports OK, MOCK or FAIL for each sponsor service. Bitdeer is live on all 5 models, x402 is live on Base Sepolia, and Agentverse accepts our key. CRS goes on hold.
- **E2, chat agents:** a hello agent, the Fraud Desk and the Merchant Scanner, on the Chat Protocol with Agentverse mailboxes that register themselves on startup. The model only picks which tool to call; code formats the answer, so the numbers in a reply are always the engine's own.
- **E3, Bitdeer client:** the per-role model assignment, structured output, tool calls and a cost ledger. It works around Bitdeer's reasoning models spending the whole token budget thinking, and falls back to the mock when credits run out.
- **E4, x402 settlement:** 3 real testnet settlements. A blocked payment never settles.
- **Groundwork for M10:** the page-injection detector already separates the poisoned and clean test pages.
- **Contract notes:** wrote down 10 observations instead of editing the contracts (`CONTRACT_NOTES.md`), including two uAgents workarounds (the Python 3.14 event loop, the macOS CA bundle).

**Proof:** 152 tests passed; tx hashes in `evidence/m0/x402-settlement.md`.

**Demo angle:** ⭐ **talking points 2, 4 and 5.** The real basescan tx is our strongest "this is real" moment, and "the model routes, code answers" is a good line on reliability.

### Sat Oct 24, 02:00 · S2 Defense skeleton · Stanley
**Did:**
- The feature service, a small rule language, 4 starter rulebooks (JSON), an orchestrator that weighs the cost of each mistake, and the `check_payment` and `explain` functions.
- Then a 5-angle review with adversarial checking, which led to these fixes:
  - outside checks logged separately
  - an engine-wide lock
  - daily spend counts only settled payments
  - a $0 cap allows nothing
  - Bureau sender checks and localhost-only binding
  - a tighter injection detector with text normalization

**Proof:** `engine/tests/test_m02_defense.py`:
- at most 2% of honest shoppers flagged, at least 70% of scripted fraud caught, p95 under 50 ms
- specialists only see facts from their own area
- chat checks never change the defense
- over-cap payments are always blocked
- reworded injections are still caught

**Demo angle:** ⭐ **talking point 7.** Rulebooks are readable JSON (e.g. `velocity.structuring`: "the same person paying just under a round-number limit 3+ times in an hour"). Showing one is the easiest way to explain what a specialist does, and the before/after once S4 rewrites it.

### Sat Oct 24, 02:25 · I2 seams: Eric's integrations on the real engine · Stanley, Eric (commit 9662e97)
**Did:**
- Merged `eric/integrations` into `stanley/core-engine`.
- `engine/entrypoints.py` builds the real engine with Eric's page scanner (Apify, fixtures as fallback) and x402 settlement. It's also the factory the chat agents use with `ENGINE_SOURCE=inprocess`.
- Every allowed round payment settles in stub mode. `ENGINE_LIVE_SETTLEMENT=1` adds a live tail: one honest payment per round, for real on Base Sepolia, at a token amount. Real settlement is never inferred from wallet keys being present.
- A 3-angle adversarial review led to these fixes:
  - explaining an unknown tx is a 404, not a 500
  - an unreadable page is never reported as safe
  - the chat router recognizes real round ids
  - `SettlementResult.reason` reaches the database
  - Eric's in-process test no longer writes to Supabase

**Proof:** `engine/tests/test_integration.py` (fully offline), contract tests for every contract change; 211 tests passed.

**Demo angle:** ⭐ talking point 5. Chat answers can now come from the real engine.

### Sat Oct 24, 02:45 · R1–R5 merged · Ryan
Schema and seed data, the marketplace (catalog, 5 weird-but-legit personas, identities, mandates), dashboard v1, 6 hand-written held-out attackers, and the XGBoost voter with the static-rules and XGBoost-alone baselines.

### Sat Oct 24, 03:10 · I1 A real round on the dashboard · Stanley, Ryan
**Did:**
- Merged `ryan/data-dashboard`.
- The engine now runs on Ryan's marketplace: his catalog and legitimate shoppers, one simulated day per round, with the engine's fraud personas shopping at the same stores (`engine/sim/marketplace.py`).
- One feature vocabulary everywhere: seeded rows, benchmarks and real rounds all use the engine's features.
- The static-rules baseline is now the engine's seed rulebooks, never updated.
- The benchmark scores with the same cost matrix the engine optimizes.
- Tuning against Ryan's real personas:
  - The seed rules flagged 4.5–5.7% of his honest shoppers. Two causes:
    - the rapid-fire rule caught subscription renewals (raised from 6 to 8 payments a minute)
    - honest shoppers went over their own daily cap without being labeled a breach (his generator now respects daily caps)
  - Now 0.1–0.7% flagged, with every fraud persona still caught 76–100%.

**Proof:**
- `engine/tests/test_integration.py`: M2 holds on Ryan's marketplace across 3 seeds × 4 rounds; the static baseline and the engine agree on shared features.
- In the browser: pressing **Run round** on the dashboard ran a real round (run `i1-20261024T093951`). The overview showed FP 0.7% and $710 of fraud missed, and the stream listed Ryan's shoppers with the engine's decisions.

**Demo angle:** ⭐ the dashboard is live, not seeded. The Run round button is the beat for "watch one round happen".

### Sat Oct 24, ~03:00 · Demo trace: one payment hopping between the uAgents · Stanley (branch `stanley/uagent-trace`, not merged)
**Did:**
- `EngineBureau` takes an optional `on_hop` callback. Each agent reports a message as it arrives: who sent it, who got it, the message type and the parsed payload. Nothing else in the Bureau changed.
- `engine/trace.py` sends a card tester's 8th tiny payment in 36 s through the real Bureau and prints the 10 hops with timings, then the decision. `--scenario legit` shows an honest payment, and `--pace` sets the seconds per line for a live audience (default 0.3). Nothing is written to the database.
- The card tester's 8th payment trips the rapid-fire rule at both the old threshold (6/min) and the new one from I1 (8/min).
- Built in a separate worktree (`.worktrees/stanley-trace`, off 9662e97) so it doesn't collide with the overnight build.

**Proof:** `engine/tests/test_trace.py` checks:
- every hop is reported once, in order
- the vote payloads equal the decision's votes
- timing and velocity block the card tester
- the honest payment is allowed

The engine suite passed: 58 passed, 3 skipped.

**Demo angle:** ⭐ **talking point 1.** This is the on-screen proof that the agents talk to each other. Run it right after the problem slide.

### Sat Oct 24, 03:27 · Arena replay (3D battle view) · Stanley (branch `stanley/arena-viz`, not merged)
**Did:**
- New standalone app in `arena/` (Vite, React Three Fiber). It's separate from Ryan's `dashboard/`, so neither steps on the other.
- The layout puts the x402 gate at the centre with the 5 specialists and the orchestrator as angels around it. The 24 arena shelves are the outer ring of hex tiles: attacks on stores on one half, attacks on shoppers on the other.
- Devils throw payment coins at the gate. Angels zap the ones they block, and stolen coins flash in the vault.
- Each round plays as spawn beams (a defender building an attacker), then 3 waves with devils rethinking between them, then learning, when the shelf tiles recolour.
- Clicking anything opens a card: an attacker's origin, model, plan, tuned numbers and wave-by-wave results; a defender's question, rulebook, this round's move and next move; a shelf's catch rate and top attackers; the gate's round metrics.
- The timeline doubles as the exploitability chart.
- It plays a deterministic mock run until the engine exports a run. The replay format (`arena/src/data/types.ts`) maps field by field onto `contracts/db.sql` tables, and `arena/README.md` lists the mapping.

- The mock draws a fixed attack budget each round, like the spec: every outsider, the new attackers, 6 from the shakiest shelves and 2 at random. Archived attackers that weren't drawn wait on their shelves. An earlier version ran every archived attacker, so traffic grew 10× and losses rose even as the catch rate improved.
- The mock run (seed 7) now tells the spec's story:
  - losses fall from $1,744 in round 1 to $383 in round 20, under the M5 target of 60%
  - the catch rate climbs from 29% to the low 80s
  - the round 13 news scam spikes losses
  - the freeze drops the catch rate to 39% by round 17, and it recovers after
- The mock's payment shuffle is now deterministic across JS engines; it used `sort()` with a random comparator before.

**Proof:** `npm run build` passes. Checked in the browser at 1280×800, 1000×700 and on a phone: 120 fps, every card type opens, and the HUD doesn't overlap. Node and Chrome produce identical per-round losses from the same seed. Screenshots are in `evidence/arena/` (round 16 frozen mid-wave; round 13 Velocity angel card).

**Demo angle:** ⭐ the 40 s "arms race" beat. Play rounds 1→20 at 4–8×: shelves go red to green, the timeline bars fall, a news scam drops at round 6, and the freeze at 15–16 makes them jump while the scoreboard's catch rate drops. Then pause and click a devil to show its plan, and an angel to show its rules. 🔜 Needs an exporter from the real run tables to `arena/public/replay.json`.

---

## Entry template

```markdown
### <Day Mon DD, HH:MM> · <Milestone ID and name> · <who>
**Did:** what changed, in plain words.
**Proof:** test file, evidence file, commit or screenshot.
**Demo angle:** which talking point this backs (⭐ if it's a headline moment), or "none, behind the scenes".
```

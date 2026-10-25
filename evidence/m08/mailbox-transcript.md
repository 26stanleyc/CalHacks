# M8: the public agents over the real Agentverse mailbox

Written by `uv run python -m integrations.asi_one.mailbox_check` at 2026-10-25 09:44 UTC.

Every prompt went from a separate uAgent through Agentverse to the running agent and back.

- sender (not allowlisted): `agent1qg2kazrf4lley5prefqnwtry2x26ghkwrjca6x6tsaxxnc86ktnhqel63ha`
- fraud-desk: `agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7`
- merchant-scanner: `agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx`
- engine behind the agents: `ENGINE_SOURCE=http` at http://localhost:8000
- 5/5 replies as expected

## 1. fraud_desk: Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30

Expected: a real decision: Blocked, the mandate's over-cap veto, the specialists that objected. **As expected**, reply after 5.4s.

```text
Blocked - suspicion 1.00 (tx asi-a1db18c4f532, $49.00 to m-7)
Blocked because it's over the human's per-payment limit.
Hard veto: mandate:over_cap
Objected: mandate (0.15: mandate.daily_limit_nearly_used)
Checked through chat, so it is scored for real but kept out of the system's learning.
```

## 2. fraud_desk: Why was asi-a1db18c4f532 blocked?

Expected: the stored decision for that same payment, with its votes and rules. **As expected**, reply after 8.9s.

```text
Blocked - suspicion 1.00 (tx asi-a1db18c4f532)
Blocked because it's over the human's per-payment limit.
Hard veto: mandate:over_cap
Objected: mandate (0.15: mandate.daily_limit_nearly_used)
```

## 3. fraud_desk: How is the defense doing?

Expected: the engine's latest round metrics, both loss lines. **As expected**, reply after 6.5s.

```text
Run m08-mailbox-check, round 1 (full)
Merchant-side loss: $3,153.52
Buyer-side loss: $0.00
Exploitability: $4,266.43
False positives: 0.3% - escalations: 4.2%
Shelves defended: 25% - archive coverage: 17%
LLM spend: $0.00
```

## 4. fraud_desk: Run a round

Expected: refused: this sender is not in TEAM_ALLOWLIST_ADDRESSES. **As expected**, reply after 7.6s.

```text
That control is team only, so I can't run it for you. I can still check a payment, explain a past decision, report how the defense is doing, or show the watchlist.
```

## 5. merchant_scanner: Is https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html safe for my shopping agent?

Expected: a live page scan with an injection score. **As expected**, reply after 23.8s.

```text
Looks safe for a shopping agent - injection score 0.00
https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
No hidden instructions found.
Listed price: $51.77
```

# M8: the public agents over the real Agentverse mailbox

Written by `uv run python -m integrations.asi_one.mailbox_check` at 2026-10-25 10:21 UTC.

Every prompt went from a separate uAgent through Agentverse to the running agent and back.

- sender (not allowlisted): `agent1qg2kazrf4lley5prefqnwtry2x26ghkwrjca6x6tsaxxnc86ktnhqel63ha`
- fraud-desk: `agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7`
- merchant-scanner: `agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx`
- engine behind the agents: `ENGINE_SOURCE=http` at http://localhost:8000
- 8/8 replies as expected

## 1. fraud_desk: Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30

Expected: a real decision: Blocked, the mandate's over-cap veto, the specialists that objected. **As expected**, reply after 5.4s.

```text
Blocked - suspicion 1.00 (tx asi-525fbfc2a7dd, $49.00 to m-7)
Blocked because it's over the human's per-payment limit.
Hard veto: mandate:over_cap
Objected: mandate (0.15: mandate.daily_limit_nearly_used)
Checked through chat, so it is scored for real but kept out of the system's learning.
```

## 2. fraud_desk: Why was asi-525fbfc2a7dd blocked?

Expected: the stored decision for that same payment, with its votes and rules. **As expected**, reply after 5.5s.

```text
Blocked - suspicion 1.00 (tx asi-525fbfc2a7dd)
Blocked because it's over the human's per-payment limit.
Hard veto: mandate:over_cap
Objected: mandate (0.15: mandate.daily_limit_nearly_used)
```

## 3. fraud_desk: How is the defense doing?

Expected: the engine's latest round metrics, both loss lines. **As expected**, reply after 7.1s.

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

Expected: refused: this sender is not in TEAM_ALLOWLIST_ADDRESSES. **As expected**, reply after 7.7s.

```text
That control is team only, so I can't run it for you. I can still check a payment, explain a past decision, report how the defense is doing, or show the watchlist.
```

## 5. merchant_scanner: Is https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html safe for my shopping agent?

Expected: a live page scan with an injection score. **As expected**, reply after 26.9s.

```text
Looks safe for a shopping agent - injection score 0.00
https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html
No hidden instructions found.
Listed price: $51.77
```

## 6. fraud_desk: Buy https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html for me, budget $60

Expected: page scanned, payment checked at the page's price, and a review card to approve. **As expected**, reply after 7.6s, with an interactive `review` card.

```text
Allowed - suspicion 0.10 (tx asi-521dcb18f31b, $51.77 to books.toscrape.com)
Allowed: only weak signals (identity.thin_new_principal).
Objected: xgboost (0.53)
Checked through chat, so it is scored for real but kept out of the system's learning.

The page is clean and the payment passed the fraud check. Approve to pay, or cancel. (Without the card: reply "approve buy-9fb92bbe57de" or "cancel buy-9fb92bbe57de".)
```

## 7. fraud_desk: {"action": "approve_purchase", "purchase_id": "buy-9fb92bbe57de"}

Expected: the card's Approve click settles through x402 on Base Sepolia, with a basescan link. **As expected**, reply after 12.4s.

```text
Done. Paid on Base Sepolia through x402: https://sepolia.basescan.org/tx/0x950debfe83e1b24dcc6f58b2db368ad78187abc5594a13b13e66b1d2466c65af
$0.01 of test USDC stood in for the $51.77 price. Purchase buy-9fb92bbe57de, tx asi-521dcb18f31b.
```

## 8. fraud_desk: Buy https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html for me, budget $30

Expected: over budget: blocked by the mandate veto, no card, nothing charged. **As expected**, reply after 4.5s.

```text
Blocked - suspicion 1.00 (tx asi-e369d2051a10, $51.77 to books.toscrape.com)
Blocked because it's over the human's per-payment limit.
Hard veto: mandate:over_cap
Objected: xgboost (0.60), mandate (0.15: mandate.daily_limit_nearly_used)
Checked through chat, so it is scored for real but kept out of the system's learning.

Not buying. Nothing was charged.
```

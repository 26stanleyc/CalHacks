# M0 manual checks: what still needs a human

The automated probes are `uv run python scripts/probe_integrations.py`. Everything below
needs a person, because it is a screenshot or a third-party UI.

## 1. Chat with the hello agent in ASI:One (M0, required for the Fetch track)

Already done automatically: all three agents register their Agentverse mailbox on startup
and publish the `AgentChatProtocol` manifest, and the Fraud Desk has been verified
answering five scripted prompts over the real mailbox from a separate agent. What is left
is the ASI:One UI itself, and the screenshot.

```bash
uv run python -m integrations.asi_one.hello_agent
```

Leave it running, then:

1. Open <https://asi1.ai> and sign in with the same account the `AGENTVERSE_API_KEY` belongs to.
2. Search for **fraud-defense-hello**, or paste its address:
   `agent1qwucnzmjn7j00nullzjwyee7tutp5jyyf6cvgpc02x3nfh009n7t2ncq9vp`
3. Say "hello", then "what is this project?".
4. **Save a screenshot of the reply to `evidence/m0/asi-one-hello.png`.**

The agent's own log prints an Agentverse inspector link at startup, which is the quickest
way to confirm it is connected before opening ASI:One.

## 2. The three scripted Fraud Desk prompts (M8 manual check)

```bash
uv run python -m integrations.asi_one.fraud_desk
```

Address: `agent1q2hr8875u86ac49yd8tf7ehg5cygl2y7vzdaxse8jq0fxlwqevm0zfnz9m7`

From a fresh ASI:One session, run the three prompts M8 asks for and record it:

1. `Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30`
2. `Why was tx-1234 blocked?`
3. `How is the defense doing?`

Save the recording and the Agentverse URLs to `evidence/m08/`. Until checkpoint I2 the
answers come from `contracts.stubs.StubEngine`, so the numbers are canned; the routing,
sandboxing and allowlist are real.

Also worth doing while you are there: note the sender address ASI:One presents for you, and
add it to `TEAM_ALLOWLIST_ADDRESSES` in `.env`. Until then "run a round" is refused for
everyone, including us (see CONTRACT_NOTES.md item 8).

## 3. The Merchant Scanner

```bash
uv run python -m integrations.asi_one.merchant_scanner
```

Address: `agent1qd73ye5agcd0xleqkg6u75nsc0khr74znjeynl8x5ywedaux84ytu49snwx`

Ask: `Is https://example.com/product safe for my shopping agent?`

The scan is canned until M10, when `scan_page` becomes a real Apify scrape. The injection
detector behind it is already real and already separates the two fixture pages
(`integrations/fixtures/pages/`): poisoned scores 1.0, clean scores 0.0.

## 4. Bitdeer prices

The Bitdeer API does not expose pricing (checked: `GET /v1/models` returns ids only), so
the per-million-token prices have to be read off the console at <https://www.bitdeer.ai/>
and written into `integrations/model_prices.py`. Until then token counts are recorded and
cost is reported as $0, with the models listed under `unpriced_models`. See
`model-prices.md` and spec 4.1.

## 5. Topping up test USDC

Only if a real settlement fails for lack of funds. See `x402-settlement.md`.

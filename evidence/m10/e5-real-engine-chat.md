# E5 (partial): the chat agents on the real engine

Eric, Sat Oct 25. The engine half is verified; the screenshot half needs a human.

## What was checked

`ENGINE_SOURCE=inprocess` makes `integrations/asi_one/engine_source.get_engine()` build Stanley's
real `FraudEngine` through `engine.entrypoints.build_engine()` — the one-line I2 switch works, with
no change to the chat agents. All five scripted prompts were then run through `ChatRouter` against
that engine:

```bash
ENGINE_SOURCE=inprocess ENGINE_STORE=memory USE_MOCK_LLM=1 uv run python -c "
from integrations.asi_one.engine_source import get_engine
from integrations.asi_one.router import ChatRouter
from integrations.bitdeer import make_llm
r = ChatRouter(get_engine(), make_llm(), allowlist=set())
print(r.handle('agent1qstranger', 'how is the defense doing?'))"
```

| prompt | result |
|---|---|
| "how is the defense doing?" | real metrics, **both** loss lines (merchant-side and buyer-side) |
| "check this payment: agent ag-legit-3 paying m-voltmart $45 ... mandate under $30" | **Blocked**, hard veto `mandate:over_cap`, with the objecting lane and rule id, and the note that a chat check is scored for real but kept out of learning |
| "scan https://books.toscrape.com/..." | **live Apify scrape**, injection score 0.00, listed price read off the page |
| "run a round" | refused: team only (the sender is not on the allowlist) |
| "what scams are going around?" | "Nothing new on the watchlist" |

So the three M10-relevant things all show up in chat: the buyer-side loss line, a real page scan of a
real URL, and the team-only refusal still holding.

## Still needs a human

1. Start the agents and chat with them from ASI:One, then save a screenshot of the five prompts:
   ```bash
   ENGINE_SOURCE=inprocess uv run python -m integrations.asi_one.fraud_desk       # port 8102
   ENGINE_SOURCE=inprocess uv run python -m integrations.asi_one.merchant_scanner # port 8103
   ```
   Each prints `Starting agent with address: agent1q...` — that address is what you search for at
   <https://asi1.ai>. Save to `evidence/m10/asi-one-fraud-desk.png`.
2. To let yourself run a round from chat, put your ASI:One sender address in
   `TEAM_ALLOWLIST_ADDRESSES` in `.env`. It is empty, so "run a round" refuses everyone, which is
   what the table above shows.

## One small thing to know

The Merchant Scanner prints "Listed price: $51.77" for a page priced **£**51.77. `PageScan` has no
currency field, so the parser reads the number correctly and throws the symbol away. Fine for the
demo (everything in the simulation is USDC), wrong for a real non-US store. CONTRACT_NOTES M10-h.

"""Fraud Desk: the facilitator's public face in ASI:One (spec 3.7).

Its main workflow goes from intent to action: "buy this for me, budget $30: <url>" scans the
page, checks the payment, asks for approval on an interactive card, and pays through x402 on
Base Sepolia (`buy.py`). It also maps chat onto the entry points `check_payment`, `explain`,
`get_metrics`, `latest_threat_intel`, and the team-only round controls `run_round` / `set_freeze`.

    uv run python -m integrations.asi_one.fraud_desk

It talks to whichever engine `ENGINE_SOURCE` selects: by default the real one served by
`uv run python -m engine.serve`. `evidence/m08/README.md` starts all of it in one command.
"""

import logging

from integrations.asi_one.agent_base import build_chat_agent
from integrations.asi_one.buy import Purchases
from integrations.asi_one.engine_source import get_engine
from integrations.asi_one.router import FRAUD_DESK_ROLE, HELP_FRAUD_DESK, ChatRouter
from integrations.bitdeer import make_llm

NAME = "fraud-desk"
PORT = 8102

DESCRIPTION = (
    "Buys safely for you over x402: scans the product page, checks the payment against your budget "
    "with five fraud specialists, asks you to approve, then pays on Base Sepolia. Also explains decisions."
)

README = """# Fraud Desk

The public face of a **self-red-teaming, multi-agent fraud defense** for agentic
commerce. It sits at the Coinbase **x402** facilitator, the checkpoint every payment
passes through, so it protects merchants from bad buyer agents *and* buyer agents from
malicious stores.

Five specialists (timing, entity graph, identity, velocity and mandate) score every
payment with rules they wrote themselves, an XGBoost model adds a vote, and borderline
cases go to a tool-using investigator.

## What you can ask

- **Buy something safely** - "buy https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html for me, budget $60".
  The page is scanned for hidden instructions aimed at shopping agents (live Apify scrape), the
  payment is checked against your budget by the full pipeline, you approve it on a review card,
  and it settles through x402 on Base Sepolia, with the basescan link in the reply. A token
  amount of test USDC stands in for the price.
- **Check a payment** - "check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30"
- **Explain a decision** - "why was tx-1234 blocked?"
- **How is the defense doing?** - losses, false positives, exploitability, spend
- **What's on the watchlist?** - scams scraped from public reports, and whether the rules built from them have caught anything

Payments you submit here are scored by the real pipeline but tagged `source=asi_one`, so
your test inputs never enter the system's training, labels or cost ledger. Round controls
are restricted to the team.
"""

STARTER_PROMPTS = [
    "Buy https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html for me, budget $60",
    "Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30",
    "Why was tx-1234 blocked?",
    "How is the defense doing?",
    "Any new scams on the watchlist?",
]


def build_router() -> ChatRouter:
    engine = get_engine()
    return ChatRouter(
        engine=engine,
        llm=make_llm(),
        agent_name="the Fraud Desk",
        role_line=FRAUD_DESK_ROLE,
        help_text=HELP_FRAUD_DESK,
        include_team_tools=True,
        purchases=Purchases(engine, _settlement()),
    )


def _settlement():
    """x402 on Base Sepolia (real when the testnet wallet is in .env, stub otherwise), or None."""
    from integrations.x402_settlement import SettlementRefused, make_settlement

    try:
        settlement = make_settlement()
    except SettlementRefused as exc:
        logging.getLogger(__name__).warning("Purchases can't settle: %s", exc)
        return None
    logging.getLogger(__name__).info("Purchases settle through x402 in %s mode", settlement.mode)
    return settlement


router = build_router()

agent = build_chat_agent(
    name=NAME,
    seed_name="fraud_desk",
    port=PORT,
    respond=router.reply,
    description=DESCRIPTION,
    readme=README,
    starter_prompts=STARTER_PROMPTS,
)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent.run()

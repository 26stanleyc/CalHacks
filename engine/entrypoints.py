"""Composition root: builds a FraudEngine with the real plug-ins from integrations/.

The engine core (everything else in engine/) depends only on contracts/. This module and
serve.py are the only places that wire in Eric's implementations:

- page scans: integrations.page_scanner.make_page_scanner() (Apify when APIFY_TOKEN is set,
  fixtures otherwise)
- settlement: integrations.x402_settlement.X402Settlement in stub mode for every allowed round
  payment, always. ENGINE_LIVE_SETTLEMENT=1 adds the demo's live tail: one honest allowed payment
  per round also settles for real on Base Sepolia, at a token amount within X402_MAX_SETTLE_CENTS.
  ENGINE_LIVE_SETTLEMENT=all settles every allowed payment for real instead, fraud that got through
  included, at 1 cent of test USDC each (the buyer wallet needs a cent per allowed payment).
- traffic: Ryan's marketplace (data/, M1) through engine.sim.marketplace.MarketplaceWorld: his
  catalog and legitimate shoppers, one simulated day per round
- models: Eric's Bitdeer client when BITDEER_API_KEY is set (and USE_MOCK_LLM isn't), falling back
  to the engine's mock brains; the mock brains alone otherwise
- voters: Ryan's XGBoost voter (bench/xgb_voter.py) next to the specialists, retrained every round
- held-out attackers: Ryan's six hand-written ones (bench/held_out), exam only

`build_engine` is also the factory Eric's chat agents look for when ENGINE_SOURCE=inprocess
(integrations/asi_one/engine_source.py).
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from contracts.api import team_allowlist
from contracts.config import flag, use_mock_llm  # also loads .env
from engine.brains import Brain, MockBrains
from engine.engine import FraudEngine
from engine.sim.world import World
from engine.store import MemoryStore, PostgresStore

log = logging.getLogger(__name__)


def _scanner(world):
    """The page scanner, layered so each kind of URL is fetched the cheapest correct way (M10).

    The simulated marketplace serves its own pages, including the malicious merchants' poisoned
    ones, and those must be read out of the world rather than over the network: they are not real
    URLs, and handing them to Apify would mean hundreds of failed scrapes a round. Real URLs - the
    Merchant Scanner chat agent's, or a store someone asks about - fall through to Apify, with the
    fixture pages behind that for when a fetch fails.
    """
    try:
        import integrations.page_scanner as page_scanner
    except ImportError:
        log.warning("integrations.page_scanner is unavailable; using the stub page scanner")
        return None
    outside = page_scanner.make_page_scanner(fixtures=Path(page_scanner.__file__).resolve().parent / "fixtures" / "pages")
    if getattr(world, "pages", None) is None:
        return outside
    from engine.sim.pages import WorldPageScanner

    return WorldPageScanner(world.pages, fallback=outside)


def _settlements():
    """(every-payment settlement, live settlement or None, live payments per round; None is all of
    them). Never infers real mode from the wallet keys being present: real only when
    ENGINE_LIVE_SETTLEMENT is explicitly on."""
    try:
        from integrations.x402_settlement import X402Settlement
    except ImportError:
        log.warning("integrations.x402_settlement is unavailable; using the stub settlement")
        return None, None, 1
    if os.environ.get("ENGINE_LIVE_SETTLEMENT", "").strip().lower() == "all":
        return X402Settlement(mode="stub"), X402Settlement(mode="real", max_cents=1), None
    live = X402Settlement(mode="real") if flag("ENGINE_LIVE_SETTLEMENT") else None
    return X402Settlement(mode="stub"), live, 1


def _brain() -> Brain:
    """Bitdeer when it's configured, with the engine's mock brains as the fallback (a plain MockLLM
    can't answer the engine's questions), and the mock brains alone otherwise."""
    if os.environ.get("LLM_PROVIDER", "").strip().lower() == "claude":  # a fast stand-in while iterating
        from integrations.claude_llm import ClaudeLLM

        return Brain(ClaudeLLM())
    if use_mock_llm():
        return Brain(MockBrains())
    try:
        from integrations.bitdeer import BitdeerError, BitdeerLLM, FallbackLLM
    except ImportError:
        log.warning("integrations.bitdeer is unavailable; using the mock brains")
        return Brain(MockBrains())
    try:
        return Brain(FallbackLLM(BitdeerLLM(), fallback=MockBrains()))
    except BitdeerError as exc:
        log.error("could not build the Bitdeer client (%s); using the mock brains", exc)
        return Brain(MockBrains())


def _voters() -> list:
    from engine.anomaly import AnomalyVoter

    try:
        from bench.xgb_voter import XGBoostVoter
    except ImportError:  # e.g. no OpenMP runtime for XGBoost (brew install libomp)
        log.warning("Ryan's XGBoost voter is unavailable; the specialists vote with the anomaly voter only")
        return [AnomalyVoter()]
    return [XGBoostVoter(), AnomalyVoter()]


def _held_out() -> list:
    try:
        from bench.held_out import held_out_attackers
    except ImportError:
        log.warning("Ryan's hand-written held-out attackers are unavailable; the exam uses the reserved-model ones only")
        return []
    return held_out_attackers()


def _intel_source():
    try:
        from integrations.threat_intel import make_intel_source
    except ImportError:
        log.warning("Eric's integrations are unavailable; threat intel comes from the pre-scraped corpus")
        return None
    return make_intel_source()


def _world(seed: int):
    try:
        from engine.sim.marketplace import MarketplaceWorld
    except ImportError:
        log.warning("data/ (Ryan's marketplace) is unavailable; using the engine's own simulated world")
        return None
    return MarketplaceWorld(seed=seed)


def build_engine() -> FraudEngine:
    store = MemoryStore() if os.environ.get("ENGINE_STORE") == "memory" else PostgresStore()
    run_id = os.environ.get("ENGINE_RUN_ID") or "dev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    seed = int(os.environ.get("ENGINE_SEED", 42))
    settlement, live, live_per_round = _settlements()
    world = _world(seed) or World(seed=seed)  # built first: the page scanner reads its pages
    engine = FraudEngine(store=store, run_id=run_id, seed=seed, allowlist=team_allowlist(), scanner=_scanner(world),
                         settlement=settlement, live_settlement=live, live_per_round=live_per_round, world=world, brain=_brain(), voters=_voters(), held_out=_held_out(), threat_intel=True,
                         threat_source=_intel_source(), transport=os.environ.get("ENGINE_TRANSPORT", "inprocess"))
    return engine.start()

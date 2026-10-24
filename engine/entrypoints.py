"""Composition root: builds a FraudEngine with the real plug-ins from integrations/.

The engine core (everything else in engine/) depends only on contracts/. This module and
serve.py are the only places that wire in Eric's implementations:

- page scans: integrations.page_scanner.make_page_scanner() (Apify when APIFY_TOKEN is set,
  fixtures otherwise)
- settlement: integrations.x402_settlement.X402Settlement in stub mode for every allowed round
  payment, always. ENGINE_LIVE_SETTLEMENT=1 adds the demo's live tail: one honest allowed payment
  per round also settles for real on Base Sepolia, at a token amount within X402_MAX_SETTLE_CENTS.
- traffic: Ryan's marketplace (data/, M1) through engine.sim.marketplace.MarketplaceWorld: his
  catalog and legitimate shoppers, one simulated day per round
- models: Eric's Bitdeer client when BITDEER_API_KEY is set (and USE_MOCK_LLM isn't), falling back
  to the engine's mock brains; the mock brains alone otherwise

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
from engine.store import MemoryStore, PostgresStore

log = logging.getLogger(__name__)


def _scanner():
    try:
        import integrations.page_scanner as page_scanner
    except ImportError:
        log.warning("integrations.page_scanner is unavailable; using the stub page scanner")
        return None
    # The fixture pages stay available as the fallback when an Apify fetch fails.
    return page_scanner.make_page_scanner(fixtures=Path(page_scanner.__file__).resolve().parent / "fixtures" / "pages")


def _settlements():
    """(every-payment settlement, live-tail settlement or None). Never infers real mode from the
    wallet keys being present: real only when ENGINE_LIVE_SETTLEMENT is explicitly on."""
    try:
        from integrations.x402_settlement import X402Settlement
    except ImportError:
        log.warning("integrations.x402_settlement is unavailable; using the stub settlement")
        return None, None
    live = X402Settlement(mode="real") if flag("ENGINE_LIVE_SETTLEMENT") else None
    return X402Settlement(mode="stub"), live


def _brain() -> Brain:
    """Bitdeer when it's configured, with the engine's mock brains as the fallback (a plain MockLLM
    can't answer the engine's questions), and the mock brains alone otherwise."""
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
    settlement, live = _settlements()
    engine = FraudEngine(store=store, run_id=run_id, seed=seed, allowlist=team_allowlist(), scanner=_scanner(),
                         settlement=settlement, live_settlement=live, world=_world(seed), brain=_brain(),
                         transport=os.environ.get("ENGINE_TRANSPORT", "inprocess"))
    return engine.start()

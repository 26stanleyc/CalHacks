"""Composition root: builds a FraudEngine with the real plug-ins from integrations/.

The engine core (everything else in engine/) depends only on contracts/. This module and
serve.py are the only places that wire in Eric's implementations:

- page scans: integrations.page_scanner.make_page_scanner() (Apify when APIFY_TOKEN is set,
  fixtures otherwise)
- settlement: integrations.x402_settlement.X402Settlement in stub mode for every allowed round
  payment, always. ENGINE_LIVE_SETTLEMENT=1 adds the demo's live tail: one honest allowed payment
  per round also settles for real on Base Sepolia, at a token amount within X402_MAX_SETTLE_CENTS.

`build_engine` is also the factory Eric's chat agents look for when ENGINE_SOURCE=inprocess
(integrations/asi_one/engine_source.py).
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from contracts.api import team_allowlist
from contracts.config import flag  # also loads .env
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


def build_engine() -> FraudEngine:
    store = MemoryStore() if os.environ.get("ENGINE_STORE") == "memory" else PostgresStore()
    run_id = os.environ.get("ENGINE_RUN_ID") or "dev-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    settlement, live = _settlements()
    engine = FraudEngine(store=store, run_id=run_id, seed=int(os.environ.get("ENGINE_SEED", 42)),
                         allowlist=team_allowlist(), scanner=_scanner(), settlement=settlement,
                         live_settlement=live, transport=os.environ.get("ENGINE_TRANSPORT", "inprocess"))
    return engine.start()

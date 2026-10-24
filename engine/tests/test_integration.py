"""Eric's integrations running against the real engine (not the stub): the I2 seams."""

import pytest

from contracts import conformance
from contracts.api import HttpEngine, create_app
from contracts.schemas import Action, Decision, SettlementResult, TrafficSource, Transaction
from engine.engine import FraudEngine
from engine.sim.world import World

TEAM = "agent1qteammember"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import integrations.settings  # noqa: F401  (runs its load_dotenv now, so the values below stick)

    # Empty, not deleted: load_dotenv() never overrides a variable that exists, even an empty one.
    for name in ("ENGINE_SOURCE", "ENGINE_LIVE_SETTLEMENT", "APIFY_TOKEN", "BITDEER_API_KEY",
                 "TESTNET_WALLET_PRIVATE_KEY", "MERCHANT_WALLET_ADDRESS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("X402_MODE", "stub")
    monkeypatch.setenv("ENGINE_STORE", "memory")
    monkeypatch.setenv("USE_MOCK_LLM", "1")


def test_inprocess_engine_source_finds_the_real_engine():
    from integrations.asi_one.engine_source import get_engine
    from integrations.page_scanner import FixturePageScanner

    engine = get_engine("inprocess")
    try:
        assert isinstance(engine, FraudEngine)
        assert isinstance(engine.scanner, FixturePageScanner)  # no Apify token: fixtures, no network
    finally:
        engine.stop()


@pytest.mark.parametrize("live", ["", "0"])
def test_build_engine_never_settles_for_real_unless_asked(monkeypatch, live):
    from engine.entrypoints import build_engine

    monkeypatch.setenv("TESTNET_WALLET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("MERCHANT_WALLET_ADDRESS", "0x" + "22" * 20)
    monkeypatch.delenv("X402_MODE")  # X402Settlement alone would infer real mode from the wallets
    monkeypatch.setenv("ENGINE_LIVE_SETTLEMENT", live)
    engine = build_engine()
    try:
        assert engine.settlement.mode == "stub" and engine.live_settlement is None
        conformance.check_engine(engine, team_sender="dashboard")
    finally:
        engine.stop()


class FakeRealSettlement:
    """Stands in for X402Settlement(mode="real") without touching the network."""

    max_cents = 100

    def __init__(self):
        self.calls: list[Transaction] = []

    def settle(self, tx: Transaction, decision: Decision) -> SettlementResult:
        self.calls.append(tx)
        return SettlementResult(tx_id=tx.tx_id, settled=True, tx_hash="0x" + "ab" * 32)


def test_the_live_tail_settles_one_honest_payment_per_round_at_a_token_amount():
    live = FakeRealSettlement()
    engine = FraudEngine(world=World(seed=42), allowlist={TEAM}, live_settlement=live)
    engine.run_round(requested_by=TEAM)
    engine.run_round(requested_by=TEAM)
    assert len(live.calls) == 2
    truth = {g.tx_id: g for g in engine.store.ground_truth}
    assert all(not truth[tx.tx_id].is_fraud and tx.amount_cents <= 100 for tx in live.calls)
    on_chain = [s for s in engine.store.settlements if s.tx_hash]
    assert len(on_chain) == 2


def test_chat_tools_check_and_explain_on_the_real_engine():
    from integrations.asi_one.tools import EntryPointTools

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM})
    engine.run_round(requested_by=TEAM)  # the engine's clock is now hours in
    before = dict(engine.pipeline.features.events["merchant_id"])
    tools = EntryPointTools(engine, allowlist={TEAM})
    result = tools.call("check_payment", {"amount_usd": 49, "merchant_id": "m-7", "per_payment_cap_usd": 30,
                                          "request_text": "a USB-C charger under $30"}, sender="agent1qstranger")
    decision = result.data
    assert decision.action == Action.BLOCK and decision.vetoed_by == "mandate:over_cap"
    assert dict(engine.pipeline.features.events["merchant_id"]) == before  # chat never changes state
    assert tools.call("explain", {"tx_id": decision.tx_id}, sender="agent1qstranger").data.tx_id == decision.tx_id


def test_chat_explain_of_an_unknown_tx_says_so_instead_of_failing():
    from integrations.asi_one.tools import EntryPointTools

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM})
    result = EntryPointTools(engine, allowlist={TEAM}).call("explain", {"tx_id": "tx-1234"}, sender=TEAM)
    assert not result.ok and "no record" in result.text


def test_the_keyword_router_routes_explain_for_real_round_ids():
    from integrations.asi_one.router import ChatRouter
    from integrations.asi_one.tools import EntryPointTools

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM})
    engine.run_round(requested_by=TEAM)
    tx_id = next(d.tx_id for d in engine.store.decisions if d.action == Action.BLOCK)
    tools = EntryPointTools(engine, allowlist={TEAM})
    assert tools.call("explain", {"tx_id": tx_id}, sender=TEAM).ok
    from integrations.asi_one.router import keyword_route

    tool, args = keyword_route(f"Why was {tx_id} blocked?", ("explain", "check_payment"))
    assert tool == "explain" and args.get("tx_id") == tx_id
    assert ChatRouter  # imported to make sure the router module loads with the real engine around


def test_an_unreadable_page_is_not_reported_as_safe():
    from integrations.asi_one.tools import format_scan
    from integrations.page_scanner import ApifyPageScanner

    class Broken(ApifyPageScanner):
        def _fetch(self, url):
            raise RuntimeError("actor timed out")

    text = format_scan(Broken().scan("https://example.com/item"))
    assert "couldn't read" in text and "Looks safe" not in text


def test_an_ordinary_chat_payment_at_a_busy_merchant_is_not_flagged():
    from integrations.asi_one.tools import EntryPointTools

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM})
    engine.run_round(requested_by=TEAM)
    tools = EntryPointTools(engine, allowlist={TEAM})
    decision = tools.call("check_payment", {"amount_usd": 12, "merchant_id": "m-3"}, sender=TEAM).data
    assert decision.action == Action.ALLOW, decision.explanation


def test_chat_over_the_http_bridge_round_trips():
    from fastapi.testclient import TestClient

    from integrations.asi_one.tools import EntryPointTools

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM})
    remote = HttpEngine(client=TestClient(create_app(engine)))  # what ENGINE_SOURCE=http uses
    tools = EntryPointTools(remote, allowlist={TEAM})
    decision = tools.call("check_payment", {"amount_usd": 49, "per_payment_cap_usd": 30}, sender=TEAM).data
    assert decision.vetoed_by == "mandate:over_cap"
    assert tools.call("explain", {"tx_id": decision.tx_id}, sender=TEAM).data.tx_id == decision.tx_id
    assert not tools.call("explain", {"tx_id": "tx-missing"}, sender=TEAM).ok
    assert tools.call("run_round", {}, sender=TEAM).ok
    stored = [t for t in engine.store.transactions if t.source == TrafficSource.ASI_ONE]
    assert stored and all(t.run_id == engine.checks_run_id for t in stored)


def test_rounds_settle_only_allowed_payments_through_x402():
    from integrations.x402_settlement import X402Settlement

    engine = FraudEngine(world=World(seed=42), allowlist={TEAM}, settlement=X402Settlement(mode="stub"))
    engine.run_round(requested_by=TEAM)
    allowed = {d.tx_id for d in engine.store.decisions if d.action == Action.ALLOW}
    assert {s.tx_id for s in engine.store.settlements} == allowed
    assert all(s.settled and s.tx_hash is None and s.reason == "" for s in engine.store.settlements)


def test_x402_settlement_reports_why_a_blocked_payment_did_not_settle():
    from integrations.x402_settlement import X402Settlement

    tx = conformance.sample_tx()
    blocked = Decision(tx_id=tx.tx_id, action=Action.BLOCK, weighted_score=0.9, votes=[])
    result = X402Settlement(mode="stub").settle(tx, blocked)
    assert not result.settled and result.reason

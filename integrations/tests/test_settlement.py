"""x402 settlement (milestone E4): the conformance check plus the rules around it."""

import pytest

from contracts.conformance import check_settlement, sample_tx
from contracts.interfaces import Settlement
from contracts.schemas import Action, Decision, TrafficSource
from integrations.settings import BASE_SEPOLIA_CAIP2
from integrations.x402_settlement import (
    SettlementRefused,
    X402Settlement,
    cents_to_usdc_units,
    explorer_url,
)


def decision_for(tx, action: Action) -> Decision:
    return Decision(tx_id=tx.tx_id, action=action, weighted_score=0.5, votes=[])


@pytest.fixture
def stub_settlement() -> X402Settlement:
    return X402Settlement(mode="stub")


# --- the shared contract --------------------------------------------------------------


def test_satisfies_the_settlement_contract(stub_settlement):
    assert isinstance(stub_settlement, Settlement)
    check_settlement(stub_settlement)


def test_conformance_check_passes_in_real_mode(monkeypatch):
    """`check_settlement` only settles a *blocked* payment, so it must need no network."""
    monkeypatch.setenv("TESTNET_WALLET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("MERCHANT_WALLET_ADDRESS", "0x" + "22" * 20)

    settlement = X402Settlement(mode="real")

    def explode(*args, **kwargs):  # pragma: no cover - fails the test if reached
        raise AssertionError("a blocked payment must not reach the facilitator")

    monkeypatch.setattr(settlement, "_facilitator_client", explode)
    monkeypatch.setattr(settlement, "_sign_payment", explode)
    check_settlement(settlement)


# --- never settle a payment the engine did not allow ---------------------------------


@pytest.mark.parametrize("action", [Action.BLOCK, Action.STEP_UP])
def test_only_an_allow_ever_settles(stub_settlement, action):
    tx = sample_tx("tx-not-allowed")
    result = stub_settlement.settle(tx, decision_for(tx, action))
    assert result.settled is False
    assert result.tx_hash is None
    assert str(action) in stub_settlement.attempts[-1].reason


def test_an_allow_settles_in_stub_mode(stub_settlement):
    tx = sample_tx("tx-allowed")
    result = stub_settlement.settle(tx, decision_for(tx, Action.ALLOW))
    assert result.settled is True
    # Stub mode must not invent a hash: a hash means a real chain transaction.
    assert result.tx_hash is None
    assert result.network == BASE_SEPOLIA_CAIP2


def test_a_decision_for_another_transaction_does_not_settle(stub_settlement):
    tx = sample_tx("tx-mine")
    other = Decision(tx_id="tx-someone-elses", action=Action.ALLOW, weighted_score=0.1, votes=[])
    assert stub_settlement.settle(tx, other).settled is False
    assert "tx-someone-elses" in stub_settlement.attempts[-1].reason


@pytest.mark.parametrize("amount", [0, -1, -5_000])
def test_a_non_positive_amount_does_not_settle(stub_settlement, amount):
    tx = sample_tx("tx-zero", amount_cents=amount)
    assert stub_settlement.settle(tx, decision_for(tx, Action.ALLOW)).settled is False


def test_an_amount_over_the_testnet_cap_does_not_settle(monkeypatch):
    """The cap guards the faucet-funded wallet, so it must stop the call before signing."""
    settlement = X402Settlement(
        mode="real", buyer_private_key="0x" + "11" * 32, merchant_address="0x" + "22" * 20, max_cents=100
    )

    def explode(*args, **kwargs):  # pragma: no cover - fails the test if reached
        raise AssertionError("an over-cap payment must not be signed or sent")

    monkeypatch.setattr(settlement, "_sign_payment", explode)
    monkeypatch.setattr(settlement, "_facilitator_client", explode)

    tx = sample_tx("tx-huge", amount_cents=10_000)
    assert settlement.settle(tx, decision_for(tx, Action.ALLOW)).settled is False
    assert "cap" in settlement.attempts[-1].reason


def test_the_cap_does_not_apply_in_stub_mode():
    """Simulated traffic runs past $1; the precompute runs are all stub mode."""
    settlement = X402Settlement(mode="stub", max_cents=100)
    tx = sample_tx("tx-big-sim", amount_cents=250_000)
    assert settlement.settle(tx, decision_for(tx, Action.ALLOW)).settled is True


def test_chat_sourced_payments_settle_by_the_same_rule(stub_settlement):
    """`source=asi_one` changes what the engine learns, not what settles."""
    tx = sample_tx("tx-chat").model_copy(update={"source": TrafficSource.ASI_ONE})
    assert stub_settlement.settle(tx, decision_for(tx, Action.ALLOW)).settled is True
    assert stub_settlement.settle(tx, decision_for(tx, Action.BLOCK)).settled is False


# --- testnet only ---------------------------------------------------------------------


@pytest.mark.parametrize("network", ["eip155:8453", "eip155:1", "base", "ethereum", "mainnet"])
def test_refuses_any_network_but_base_sepolia(network):
    with pytest.raises(SettlementRefused, match="Base Sepolia"):
        X402Settlement(mode="stub", network=network)


@pytest.mark.parametrize("network", ["eip155:84532", "base-sepolia"])
def test_accepts_base_sepolia_under_either_name(network):
    assert X402Settlement(mode="stub", network=network).network == BASE_SEPOLIA_CAIP2


def test_real_mode_needs_a_buyer_wallet():
    with pytest.raises(SettlementRefused, match="TESTNET_WALLET_PRIVATE_KEY"):
        X402Settlement(mode="real", buyer_private_key="", merchant_address="0x" + "22" * 20)


def test_real_mode_needs_a_merchant_wallet():
    with pytest.raises(SettlementRefused, match="MERCHANT_WALLET_ADDRESS"):
        X402Settlement(mode="real", buyer_private_key="0x" + "11" * 32, merchant_address="")


def test_an_unknown_mode_is_refused():
    with pytest.raises(SettlementRefused, match="stub"):
        X402Settlement(mode="whatever")


# --- mode selection and reporting -----------------------------------------------------


def test_mode_defaults_to_stub_without_wallets():
    assert X402Settlement().mode == "stub"


def test_x402_mode_env_wins(monkeypatch):
    monkeypatch.setenv("X402_MODE", "stub")
    monkeypatch.setenv("TESTNET_WALLET_PRIVATE_KEY", "0x" + "11" * 32)
    monkeypatch.setenv("MERCHANT_WALLET_ADDRESS", "0x" + "22" * 20)
    assert X402Settlement().mode == "stub"


def test_cents_convert_to_usdc_base_units():
    # USDC has 6 decimals, so one cent is 10_000 units and one dollar is 1_000_000.
    assert cents_to_usdc_units(1) == 10_000
    assert cents_to_usdc_units(100) == 1_000_000
    assert cents_to_usdc_units(4_900) == 49_000_000


def test_summary_counts_settled_and_refused(stub_settlement):
    allowed = sample_tx("tx-a")
    blocked = sample_tx("tx-b")
    stub_settlement.settle(allowed, decision_for(allowed, Action.ALLOW))
    stub_settlement.settle(blocked, decision_for(blocked, Action.BLOCK))
    summary = stub_settlement.summary()
    assert summary == {
        "mode": "stub",
        "network": BASE_SEPOLIA_CAIP2,
        "attempts": 2,
        "settled": 1,
        "refused": 1,
        "hashes": [],
    }


def test_explorer_url_points_at_base_sepolia():
    assert explorer_url("0xabc") == "https://sepolia.basescan.org/tx/0xabc"

"""`contracts.interfaces.Settlement` on Coinbase x402, Base Sepolia only.

The orchestrator decides; this module only carries out an `allow`. The one invariant it
enforces for itself is the conformance rule in `contracts/conformance.py`:

    a payment whose decision is not `allow` is never settled, and no network call is made.

Two modes (`X402_MODE`, or inferred from whether the wallet env vars are set):

- `stub` — no network at all. Used for the precompute runs, where hundreds of rounds of
  simulated payments must not touch a chain (spec 4.1).
- `real` — signs an EIP-3009 `transferWithAuthorization` with the testnet buyer wallet
  and settles it through the x402 facilitator, which pays the gas. Used for the live
  demo (M13), where one real hash has to open on a block explorer.

Testnet only, by construction: `_require_testnet` refuses any network but Base Sepolia,
and in real mode `max_settle_cents` caps a single payment so a bug cannot drain the faucet
balance. The cap does not apply in stub mode, where no wallet is involved and simulated
amounts run well past it.
Set up the wallet with `scripts/make_local_wallet.py`; top it up with `scripts/fund_wallet.py`.
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from contracts.schemas import Action, Decision, SettlementResult, Transaction
from integrations.settings import (
    BASE_SEPOLIA_CAIP2,
    BASE_SEPOLIA_EXPLORER,
    BASE_SEPOLIA_V1_NAME,
    USDC_UNITS_PER_CENT,
    facilitator_url,
    max_settle_cents,
    x402_mode,
)

logger = logging.getLogger(__name__)

# How long the signed authorization stays valid. The facilitator waits for a block, so
# give it room; the client timeout below is deliberately longer still.
AUTHORIZATION_VALIDITY_SECONDS = 600
FACILITATOR_TIMEOUT_SECONDS = 90.0


class SettlementRefused(RuntimeError):
    """The settlement layer refused to act: wrong network, no wallet, over the cap."""


@dataclass
class SettlementAttempt:
    """What happened on one `settle` call.

    `SettlementResult` has no field for *why* a settlement did not happen (see
    CONTRACT_NOTES.md), so the reason is kept here for the logs and the demo.
    """

    tx_id: str
    settled: bool
    action: Action
    mode: str
    tx_hash: str | None = None
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def explorer_url(self) -> str | None:
        return f"{BASE_SEPOLIA_EXPLORER}/tx/{self.tx_hash}" if self.tx_hash else None


def explorer_url(tx_hash: str) -> str:
    return f"{BASE_SEPOLIA_EXPLORER}/tx/{tx_hash}"


def cents_to_usdc_units(cents: int) -> int:
    """Integer cents of USDC to USDC base units (6 decimals)."""
    return int(cents) * USDC_UNITS_PER_CENT


class X402Settlement:
    """Settles allowed payments through the x402 facilitator.

    >>> settlement = X402Settlement(mode="stub")
    >>> settlement.settle(tx, allowed_decision).settled
    True
    """

    def __init__(
        self,
        *,
        mode: str | None = None,
        buyer_private_key: str | None = None,
        merchant_address: str | None = None,
        facilitator: str | None = None,
        network: str = BASE_SEPOLIA_CAIP2,
        max_cents: int | None = None,
    ) -> None:
        self.mode = (mode or x402_mode()).strip().lower()
        if self.mode not in ("stub", "real"):
            raise SettlementRefused(f"X402_MODE must be 'stub' or 'real', not {self.mode!r}")
        self.network = _require_testnet(network)
        self.facilitator_url = facilitator or facilitator_url()
        self.max_cents = max_cents if max_cents is not None else max_settle_cents()
        self._buyer_private_key = (
            buyer_private_key if buyer_private_key is not None else os.environ.get("TESTNET_WALLET_PRIVATE_KEY", "")
        )
        self.merchant_address = (
            merchant_address if merchant_address is not None else os.environ.get("MERCHANT_WALLET_ADDRESS", "")
        )
        self.attempts: list[SettlementAttempt] = []
        self._lock = threading.Lock()
        self._client = None  # built on first real settlement

        if self.mode == "real" and not self._buyer_private_key:
            raise SettlementRefused(
                "X402_MODE=real needs TESTNET_WALLET_PRIVATE_KEY; run scripts/make_local_wallet.py"
            )
        if self.mode == "real" and not self.merchant_address:
            raise SettlementRefused("X402_MODE=real needs MERCHANT_WALLET_ADDRESS")

    # -- the contract ------------------------------------------------------------------

    def settle(self, tx: Transaction, decision: Decision) -> SettlementResult:
        """Settle an allowed payment. A blocked or stepped-up one never settles."""
        attempt = self._settle(tx, decision)
        with self._lock:
            self.attempts.append(attempt)
        if not attempt.settled:
            logger.info("x402 did not settle %s (%s): %s", tx.tx_id, attempt.action, attempt.reason)
        return SettlementResult(
            tx_id=tx.tx_id,
            settled=attempt.settled,
            tx_hash=attempt.tx_hash,
            network=self.network,
        )

    # -- internals ---------------------------------------------------------------------

    def _settle(self, tx: Transaction, decision: Decision) -> SettlementAttempt:
        def refused(reason: str, **detail: Any) -> SettlementAttempt:
            return SettlementAttempt(
                tx_id=tx.tx_id, settled=False, action=decision.action, mode=self.mode, reason=reason, detail=detail
            )

        # The invariant, checked before anything else happens.
        if decision.action != Action.ALLOW:
            return refused(f"decision was {decision.action}, not allow")
        if decision.tx_id != tx.tx_id:
            return refused(f"decision is for {decision.tx_id}, not {tx.tx_id}")
        if tx.amount_cents <= 0:
            return refused(f"amount must be positive, got {tx.amount_cents}")

        if self.mode == "stub":
            return SettlementAttempt(
                tx_id=tx.tx_id, settled=True, action=decision.action, mode="stub", reason="stub mode: not settled on chain"
            )

        # Only real mode has a faucet-funded wallet to protect. Applying this in stub mode
        # would refuse ordinary simulated traffic, which runs well past $1 (the archive has
        # an "over $100" amount band), and the precompute runs are all stub mode.
        if tx.amount_cents > self.max_cents:
            return refused(
                f"{tx.amount_cents} cents is over the {self.max_cents} cent testnet cap "
                "(raise X402_MAX_SETTLE_CENTS if that is intended)"
            )
        return self._settle_on_chain(tx, decision)

    def _settle_on_chain(self, tx: Transaction, decision: Decision) -> SettlementAttempt:
        try:
            payload, requirements = self._sign_payment(tx)
        except Exception as exc:  # signing is local; a failure here is a bug or bad config
            logger.exception("Could not sign the x402 payment for %s", tx.tx_id)
            return SettlementAttempt(
                tx_id=tx.tx_id, settled=False, action=decision.action, mode="real", reason=f"signing failed: {exc}"
            )

        client = self._facilitator_client()

        # Verify before settling: the facilitator tells us up front whether the
        # authorization is good, so a doomed settle never goes out.
        try:
            verified = client.verify(payload, requirements)
        except Exception as exc:
            return SettlementAttempt(
                tx_id=tx.tx_id, settled=False, action=decision.action, mode="real", reason=f"verify call failed: {exc}"
            )
        if not getattr(verified, "is_valid", False):
            reason = getattr(verified, "invalid_reason", None) or getattr(verified, "invalid_message", None) or "unknown"
            return SettlementAttempt(
                tx_id=tx.tx_id,
                settled=False,
                action=decision.action,
                mode="real",
                reason=f"facilitator rejected the payment: {reason}",
                detail={"hint": _funding_hint(str(reason))},
            )

        try:
            settled = client.settle(payload, requirements)
        except Exception as exc:
            return SettlementAttempt(
                tx_id=tx.tx_id, settled=False, action=decision.action, mode="real", reason=f"settle call failed: {exc}"
            )

        if not getattr(settled, "success", False):
            reason = getattr(settled, "error_reason", None) or getattr(settled, "error_message", None) or "unknown"
            return SettlementAttempt(
                tx_id=tx.tx_id,
                settled=False,
                action=decision.action,
                mode="real",
                reason=f"settlement failed: {reason}",
                detail={"hint": _funding_hint(str(reason))},
            )

        tx_hash = getattr(settled, "transaction", None) or None
        return SettlementAttempt(
            tx_id=tx.tx_id,
            settled=True,
            action=decision.action,
            mode="real",
            tx_hash=tx_hash,
            reason="settled on Base Sepolia",
            detail={"payer": getattr(settled, "payer", None), "explorer": explorer_url(tx_hash) if tx_hash else None},
        )

    def _sign_payment(self, tx: Transaction):
        """Build the payment requirements for this transaction and sign them as the buyer."""
        from eth_account import Account
        from x402.mechanisms.evm import get_default_asset
        from x402.mechanisms.evm.exact.client import ExactEvmScheme
        from x402.schemas.payments import PaymentPayload, PaymentRequirements

        asset = get_default_asset(self.network)  # USDC on Base Sepolia; resolved locally
        requirements = PaymentRequirements(
            scheme="exact",
            network=self.network,
            asset=asset["asset"],
            amount=str(cents_to_usdc_units(tx.amount_cents)),
            pay_to=self.merchant_address,
            max_timeout_seconds=AUTHORIZATION_VALIDITY_SECONDS,
            # name and version are the token's EIP-712 domain; passing them keeps
            # signing offline instead of reading them from the chain.
            extra={"name": asset["name"], "version": asset["version"]},
        )
        account = Account.from_key(self._buyer_private_key)
        inner = ExactEvmScheme(account).create_payment_payload(requirements)
        return PaymentPayload(x402_version=2, payload=inner, accepted=requirements), requirements

    def _facilitator_client(self):
        if self._client is None:
            from x402.http.facilitator_client import HTTPFacilitatorClientSync

            self._client = HTTPFacilitatorClientSync(
                {"url": self.facilitator_url, "timeout": FACILITATOR_TIMEOUT_SECONDS}
            )
        return self._client

    # -- reporting ---------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self._lock:
            attempts = list(self.attempts)
        settled = [a for a in attempts if a.settled]
        return {
            "mode": self.mode,
            "network": self.network,
            "attempts": len(attempts),
            "settled": len(settled),
            "refused": len(attempts) - len(settled),
            "hashes": [a.tx_hash for a in settled if a.tx_hash],
        }


def _require_testnet(network: str) -> str:
    """Base Sepolia or nothing. Guards against a mainnet network id reaching this layer."""
    if network in (BASE_SEPOLIA_CAIP2, BASE_SEPOLIA_V1_NAME):
        return BASE_SEPOLIA_CAIP2
    raise SettlementRefused(
        f"this layer settles on Base Sepolia ({BASE_SEPOLIA_CAIP2}) only, not {network!r}; testnet funds only"
    )


def _funding_hint(reason: str) -> str:
    if "balance" in reason.lower() or "insufficient" in reason.lower():
        return "the buyer wallet is short of test USDC: run scripts/fund_wallet.py"
    return ""


def make_settlement(**kwargs: Any) -> X402Settlement:
    """The settlement the orchestrator should use; mode comes from config."""
    return X402Settlement(**kwargs)

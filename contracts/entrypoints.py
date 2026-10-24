"""Functions the engine exposes. Eric's chat agents and Ryan's dashboard buttons call these (spec 3.7)."""

from typing import Protocol, runtime_checkable

from contracts.schemas import Decision, Mandate, PageScan, RoundMetrics, ThreatIntelItem, Transaction


class NotAllowed(Exception):
    """Raised when a sender outside TEAM_ALLOWLIST_ADDRESSES asks for a round control."""


@runtime_checkable
class Engine(Protocol):
    def check_payment(self, tx: Transaction, mandate: Mandate | None = None) -> Decision:
        """Score one payment through the real decision path. Chat-sourced payments
        (source=asi_one) are never used for labels, reflection, the cost ledger or the archive."""
        ...

    def explain(self, tx_id: str) -> Decision:
        """The logged decision for a past transaction, with votes, rules fired and explanation."""
        ...

    def scan_page(self, url: str) -> PageScan: ...

    def get_metrics(self, run_id: str | None = None, round: int | None = None) -> RoundMetrics:
        """Metrics for a round (latest round of the latest run by default)."""
        ...

    def run_round(self, requested_by: str) -> RoundMetrics:
        """Team-only: raises NotAllowed for senders outside the allowlist."""
        ...

    def set_freeze(self, on: bool, requested_by: str) -> bool:
        """Team-only. Returns the new freeze state."""
        ...

    def latest_threat_intel(self, limit: int = 5) -> list[ThreatIntelItem]: ...

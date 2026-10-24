"""Plug shapes: one person builds the part, another plugs it in (spec 8.4).

| Interface         | Built by                              | Plugged into                     |
|-------------------|---------------------------------------|----------------------------------|
| Voter             | Ryan (XGBoost voter)                  | Stanley's orchestrator           |
| Attacker          | Eric (merchant, threat intel), Ryan (held-out) | Stanley's arena and exam |
| PageScanner       | Eric (Apify page scans)               | Stanley's features, investigator |
| ThreatIntelSource | Eric (news scraper)                   | Stanley's watch rules            |
| Settlement        | Eric (x402)                           | Stanley's orchestrator           |
| Defense           | Stanley (full system), Ryan (baselines) | Ryan's benchmark runner        |
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from contracts.schemas import (
    AttackerRecord,
    AttackFeedback,
    Decision,
    FeatureVector,
    GroundTruth,
    PageScan,
    SettlementResult,
    ThreatReport,
    Transaction,
    Vote,
)


@runtime_checkable
class Voter(Protocol):
    name: str

    def score(self, tx: Transaction, features: FeatureVector) -> Vote:
        """Milliseconds, no LLM calls. Must return a Vote with voter == self.name."""
        ...

    def retrain(self, examples: list[tuple[FeatureVector, bool]]) -> None:
        """Called between rounds with (features, is_fraud) for every labeled training transaction."""
        ...


@runtime_checkable
class Attacker(Protocol):
    def record(self) -> AttackerRecord: ...

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        """Emit up to n transactions from the current playbook, deterministically for a given seed."""
        ...

    def observe(self, feedback: list[AttackFeedback]) -> None:
        """Outcomes of this attacker's transactions after a wave. Outsiders get objected_by=None."""
        ...


@runtime_checkable
class PageScanner(Protocol):
    def scan(self, url: str) -> PageScan: ...


@runtime_checkable
class ThreatIntelSource(Protocol):
    def fetch_new(self, since: datetime | None = None) -> list[ThreatReport]:
        """New public scam reports since `since` (live scrape, falling back to the corpus)."""
        ...


@runtime_checkable
class Settlement(Protocol):
    def settle(self, tx: Transaction, decision: Decision) -> SettlementResult:
        """Settle allowed payments via x402; never settles a blocked one."""
        ...


@runtime_checkable
class Defense(Protocol):
    """One benchmark contender: the full system, a baseline (static rules, XGBoost alone) or an
    ablation. The benchmark runner feeds every contender identical traffic (spec 5.1)."""

    name: str  # becomes RoundMetrics.defense, e.g. "full", "static_rules", "xgboost", "no_xgb_voter"

    def decide(self, tx: Transaction, features: FeatureVector) -> Decision: ...

    def learn(self, round: int, labeled: list[tuple[Transaction, FeatureVector, GroundTruth]]) -> None:
        """Called once after each round with that round's labeled training traffic. Static
        contenders do nothing; never called with held-out or chat-sourced transactions."""
        ...

    def freeze(self, on: bool) -> None:
        """While frozen, learn() must not change the contender (used for exploitability exams)."""
        ...

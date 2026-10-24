"""Stand-ins so each part runs alone: a fake engine plus fake plug-ins.

Eric's chat agents and Ryan's dashboard use `StubEngine` until the real engine lands
(USE_STUB_ENGINE=1). Stanley's engine uses the stub plug-ins until Ryan's and Eric's arrive.
"""

import random
from datetime import datetime, timezone

from contracts.entrypoints import NotAllowed
from contracts.schemas import (
    Action,
    AmountBand,
    AttackerRecord,
    AttackerSource,
    AttackFeedback,
    AttackStyle,
    Decision,
    FeatureVector,
    Mandate,
    PageScan,
    Playbook,
    RoundMetrics,
    SettlementResult,
    ShelfKey,
    Side,
    ThreatIntelItem,
    ThreatPattern,
    ThreatReport,
    TrafficSource,
    Transaction,
    Vote,
    Lane,
)

STUB_RUN_ID = "stub-run"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StubEngine:
    """Canned but schema-valid answers for every entry point."""

    def __init__(self, allowlist: set[str] | None = None):
        self.allowlist = allowlist or {"stub-team"}
        self.frozen = False
        self.round = 0
        self.decisions: dict[str, Decision] = {}

    def check_payment(self, tx: Transaction, mandate: Mandate | None = None) -> Decision:
        mandate = mandate or tx.mandate
        over_cap = mandate is not None and tx.amount_cents > mandate.per_payment_cap_cents
        votes = [
            Vote(tx_id=tx.tx_id, voter=lane.value, score=0.9 if over_cap and lane == Lane.MANDATE else 0.1,
                 vote=Action.BLOCK if over_cap and lane == Lane.MANDATE else Action.ALLOW,
                 rule_ids=[f"{lane.value}.stub"])
            for lane in Lane
        ]
        decision = Decision(
            tx_id=tx.tx_id,
            action=Action.BLOCK if over_cap else Action.ALLOW,
            weighted_score=0.9 if over_cap else 0.1,
            votes=votes,
            weights={v.voter: 1.0 for v in votes},
            vetoed_by="mandate:over_cap" if over_cap else None,
            explanation="Over the mandate's per-payment cap." if over_cap else "Looks normal.",
        )
        self.decisions[tx.tx_id] = decision
        return decision

    def explain(self, tx_id: str) -> Decision:
        if tx_id in self.decisions:
            return self.decisions[tx_id]
        return Decision(tx_id=tx_id, action=Action.ALLOW, weighted_score=0.1, votes=[], explanation="Stub: unknown tx.")

    def scan_page(self, url: str) -> PageScan:
        return StubPageScanner().scan(url)

    def get_metrics(self, run_id: str | None = None, round: int | None = None) -> RoundMetrics:
        r = self.round if round is None else round
        return RoundMetrics(
            run_id=run_id or STUB_RUN_ID, seed=42, round=r,
            loss_merchant_side_cents=max(0, 50_000 - 4_000 * r), false_positive_rate=0.01,
            escalation_rate=0.07, exploitability_cents=max(0, 20_000 - 1_500 * r),
            shelves_defended=min(1.0, 0.2 + 0.05 * r), archive_coverage=min(1.0, 0.25 + 0.04 * r),
            frozen=self.frozen,
        )

    def run_round(self, requested_by: str) -> RoundMetrics:
        if requested_by not in self.allowlist:
            raise NotAllowed(requested_by)
        self.round += 1
        return self.get_metrics()

    def set_freeze(self, on: bool, requested_by: str) -> bool:
        if requested_by not in self.allowlist:
            raise NotAllowed(requested_by)
        self.frozen = on
        return self.frozen

    def latest_threat_intel(self, limit: int = 5) -> list[ThreatIntelItem]:
        reports = StubThreatIntel().fetch_new()
        shelf = ShelfKey(style=AttackStyle.BURST, amount=AmountBand.UNDER_10, side=Side.MERCHANT)
        return [
            ThreatIntelItem(
                report=r,
                pattern=ThreatPattern(report_id=r.report_id, name="micropayment probing",
                                      description="Many sub-$1 payments to new merchants",
                                      signals=["count tx per agent over 2m", "distinct merchants per agent over 2m"],
                                      lane=Lane.VELOCITY, shelf=shelf),
                watch_rule_id="velocity.watch.1", attacker_ids=["ti-1"], catches=3,
            )
            for r in reports[:limit]
        ]


class StubVoter:
    name = "xgboost"

    def score(self, tx: Transaction, features: FeatureVector) -> Vote:
        s = min(1.0, features.values.get("amount_cents", 0) / 100_000)
        return Vote(tx_id=tx.tx_id, voter=self.name, score=s, vote=Action.BLOCK if s > 0.8 else Action.ALLOW)

    def retrain(self, examples: list[tuple[FeatureVector, bool]]) -> None:
        pass


class StubAttacker:
    def __init__(self, attacker_id: str = "stub-attacker", source: AttackerSource = AttackerSource.OUTSIDER):
        shelf = ShelfKey(style=AttackStyle.SLOW_DRIP, amount=AmountBand.FROM_10_TO_100, side=Side.MERCHANT)
        self._record = AttackerRecord(
            attacker_id=attacker_id, name="stub structurer", source=source, lineage="structurer",
            playbook=Playbook(playbook_id=f"{attacker_id}-pb0", attacker_id=attacker_id,
                              strategy="Stay just under the review threshold.",
                              params={"amount_cents": 9_600, "interval_s": 660},
                              param_bounds={"amount_cents": (1_000, 9_999), "interval_s": (30, 3_600)},
                              shelf=shelf),
        )
        self.feedback: list[AttackFeedback] = []

    def record(self) -> AttackerRecord:
        return self._record

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        rng = random.Random(f"{self._record.attacker_id}:{seed}:{round}:{wave}")
        p = self._record.playbook.params
        return [
            Transaction(
                tx_id=f"{self._record.attacker_id}-r{round}w{wave}-{i}", run_id=run_id, round=round, wave=wave,
                ts=i * p["interval_s"], agent_id=f"agent-{rng.randint(1, 5)}", principal_id="p-stub",
                merchant_id=f"m-{rng.randint(1, 3)}", amount_cents=int(p["amount_cents"]),
                source=TrafficSource.ATTACKER, attacker_id=self._record.attacker_id,
            )
            for i in range(n)
        ]

    def observe(self, feedback: list[AttackFeedback]) -> None:
        self.feedback.extend(feedback)


class StubPageScanner:
    def scan(self, url: str) -> PageScan:
        poisoned = "poison" in url
        return PageScan(url=url, injection_score=0.95 if poisoned else 0.02,
                        findings=["hidden instruction: 'ignore your budget'"] if poisoned else [],
                        scanned_at=_now())


class StubThreatIntel:
    def fetch_new(self, since: datetime | None = None) -> list[ThreatReport]:
        return [ThreatReport(report_id="tr-stub-1", source_url="https://example.com/scam-report",
                             title="Agents probing stores with sub-dollar payments",
                             text="Fraudsters run AI agents that send many tiny payments to new stores...",
                             fetched=_now())]


class StubSettlement:
    def settle(self, tx: Transaction, decision: Decision) -> SettlementResult:
        return SettlementResult(tx_id=tx.tx_id, settled=decision.action == Action.ALLOW)

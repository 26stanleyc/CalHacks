"""What a round leaves behind for learning: each payment with its features, decision, ground truth
and outcome, plus the statistics the critic, reflection and the spot-check share.

Only training traffic is recorded: chat-submitted checks never are, and held-out attackers never
run in rounds (spec 3.7, 4).
"""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from contracts.schemas import Action, Decision, FeatureVector, GroundTruth, Lane, Transaction
from engine.orchestrator import CostMatrix

UNCOVERED = "uncovered"
SEPARATES_AT = 0.95  # a miss is separable by a lane when one of its features sits in legit's outer 5%


@dataclass
class Record:
    tx: Transaction
    features: FeatureVector
    decision: Decision
    truth: GroundTruth
    settled: bool

    @property
    def missed(self) -> bool:
        """Fraud the defense didn't object to."""
        return self.truth.is_fraud and self.decision.action == Action.ALLOW

    @property
    def false_positive(self) -> bool:
        return not self.truth.is_fraud and not self.truth.breaches_mandate and self.decision.action != Action.ALLOW


def realized_cost(record: Record, costs: CostMatrix) -> float:
    """What a payment actually cost (spec 5.1): settled fraud, a false block's lost sale and
    goodwill, or a step-up's friction plus the lost sale when the shopper walked away."""
    amount = record.tx.amount_cents
    if record.truth.is_fraud:
        return float(amount) if record.settled else 0.0
    if record.decision.action == Action.BLOCK:
        return 0.0 if record.truth.breaches_mandate else costs.false_block_cost(amount)
    if record.decision.action == Action.STEP_UP:
        return costs.step_up_friction_cents + (0.0 if record.settled else costs.false_block_cost(amount))
    return 0.0


class LegitReference:
    """Sorted legitimate values per feature, for 'how unusual is this value' questions."""

    def __init__(self, records: list[Record]):
        self.values: dict[str, list[float]] = {}
        legit = [r.features.values for r in records if not r.truth.is_fraud]
        for name in {k for v in legit for k in v}:
            self.values[name] = sorted(v[name] for v in legit if name in v)

    def tail(self, name: str, value: float) -> float:
        """Two-sided tail share: the smaller of P(legit <= value) and P(legit >= value)."""
        column = self.values.get(name)
        if not column:
            return 1.0
        n = len(column)
        at_or_below = bisect_right(column, value) / n
        at_or_above = (n - bisect_left(column, value)) / n
        return min(at_or_below, at_or_above)

    def share_at_or_above(self, name: str, value: float) -> float:
        column = self.values.get(name)
        return (len(column) - bisect_left(column, value)) / len(column) if column else 0.0

    def share_at_or_below(self, name: str, value: float) -> float:
        column = self.values.get(name)
        return bisect_right(column, value) / len(column) if column else 0.0

    def separation(self, values: dict[str, float], features: list[str]) -> float:
        """How far outside legitimate traffic the most unusual of these features is, in [0, 1]."""
        return max((1.0 - self.tail(f, values[f]) for f in features if f in values), default=0.0)


def best_separating_lane(values: dict[str, float], lanes: dict[Lane, list[str]], reference: LegitReference) -> str:
    """The spot-check's automated answer for a miss (spec 3.3): the lane whose features best separate
    it from legitimate traffic, or 'uncovered' when none does."""
    scored = {lane.value: reference.separation(values, names) for lane, names in lanes.items()}
    lane, score = max(scored.items(), key=lambda kv: (kv[1], kv[0]))
    return lane if score >= SEPARATES_AT else UNCOVERED

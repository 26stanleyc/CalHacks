"""Hedge (multiplicative weights) on the orchestrator's voter weights (spec 4.9).

Each voter, the XGBoost voter included, is charged what its own vote would have cost on each of
the round's payments under the cost matrix: a missed fraud costs its amount, a false block the
lost sale, a step-up its friction. Weights shrink by exp(-eta * loss / worst loss), are rescaled
so the best voter weighs 1, and never drop under a floor, so a voter can always earn its way back.
"""

import math
from collections import defaultdict

from engine.learning.labels import Record
from engine.orchestrator import CostMatrix

ETA = 0.5
FLOOR = 0.05


def update(weights: dict[str, float], records: list[Record], costs: CostMatrix, eta: float = ETA,
           floor: float = FLOOR) -> dict[str, float]:
    loss: dict[str, float] = defaultdict(float)
    for r in records:
        if r.truth.breaches_mandate and not r.truth.is_fraud:
            continue  # blocked by the mandate veto whatever anyone voted
        truth = 1.0 if r.truth.is_fraud else 0.0
        for vote in r.decision.votes:
            loss[vote.voter] += costs.expected_costs(truth, r.tx.amount_cents)[vote.vote]
    if not loss:
        return dict(weights)
    worst = max(loss.values()) or 1.0
    new = {voter: weights.get(voter, 1.0) * math.exp(-eta * cost / worst) for voter, cost in loss.items()}
    top = max(new.values())
    return {voter: round(max(floor, w / top), 6) for voter, w in new.items()}

"""The orchestrator's learned combiner (stacking): turns the voters' scores into a calibrated
probability of fraud, which the cost matrix then turns into allow / step-up / block.

The fixed noisy-OR (engine/orchestrator.py) treats its output as a probability, but it isn't one,
so the cost math decides too timidly, and it can't learn that two mild signals together mean fraud.
This model learns both from labels:

- Trained between rounds on the votes the defense actually cast. Every score in it came from the
  rulebooks and models of its time, before they saw that payment's label: out of sample in time,
  which is the condition that matters (last round's model facing this round's adapted attackers).
  Training on freshly retrained XGBoost scores would teach it to trust XGBoost blindly; random
  out-of-fold scores (OUT_OF_FOLD, off) were tried and are too optimistic: same round, same attackers.
- Inputs: each known voter's score (a missing voter counts 0), the pairwise products of the scores
  (two weak signals together), and the log of the amount. (Log-odds inputs, LOGIT_INPUTS, were tried:
  no ranking gain, and more extreme scores made the FP cap less stable.)
- Model: logistic regression, which is reasonably calibrated out of the box.
- The false-positive cap still rules: after fitting, the smallest shift toward "allow" is kept that
  keeps the calibration rounds (pooled) under `target_fpr`. The calibration rounds are recent honest
  payments re-scored by the defense as it's about to be deployed (new rulebooks included), so a rule
  reflection just added can't push honest payments over the cap through the combiner.

Hard vetoes, the watch-rule guard and escalation are unchanged: they sit around this model.
"""

import math
from itertools import combinations

import numpy as np
from sklearn.linear_model import LogisticRegression

from contracts.schemas import Action, Vote
from engine.orchestrator import decision_costs

MIN_ROUNDS = 2
OUT_OF_FOLD = False  # see the module docstring and evidence/stacking/strategy-log.md (S2)
LOGIT_INPUTS = False  # S3 in the strategy log
POOLED_CALIBRATION = True  # judge the calibration rounds pooled (~900 honest payments), not by the noisier worst round (G4)
MIN_PER_CLASS = 30
WINDOW_ROUNDS = 6  # recent rounds only: rulebooks change, so old votes describe an older defense
SHIFTS = [x / 4 for x in range(0, 33)]  # logit shifts toward allow, 0 to 8


class StackedCombiner:
    def __init__(self, voters: list[str], model: LogisticRegression, shift: float, version: int):
        self.voters = voters
        self.model = model
        self.shift = shift
        self.version = version
        # the fitted weights, applied directly (scikit-learn's per-call overhead adds up over the
        # arena's tens of thousands of practice payments a round)
        self._coef = model.coef_[0].astype(np.float64)
        self._intercept = float(model.intercept_[0])

    @staticmethod
    def row(voters: list[str], votes: list[Vote], amount_cents: int) -> list[float]:
        by = {v.voter: v.score for v in votes}
        s = [float(by.get(name, 0.0)) for name in voters]
        logits = [math.log((x + 0.01) / (1.01 - x)) / 5 for x in s] if LOGIT_INPUTS else []
        return s + logits + [a * b for a, b in combinations(s, 2)] + [math.log1p(max(0, amount_cents)) / 10]

    def probability(self, votes: list[Vote], amount_cents: int, shifted: bool = True) -> float:
        """shifted=False gives the calibrated probability before the false-positive shift."""
        z = float(np.dot(self._coef, self.row(self.voters, votes, amount_cents))) + self._intercept - (self.shift if shifted else 0.0)
        return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def _shifted(p: float, shift: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return 1.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) - shift)))


def _fit(voters: list[str], rows: list[tuple]) -> LogisticRegression | None:
    labels = np.array([y for _, _, y in rows], dtype=np.int32)
    if labels.sum() < MIN_PER_CLASS or (len(labels) - labels.sum()) < MIN_PER_CLASS:
        return None
    X = np.array([StackedCombiner.row(voters, votes, amount) for votes, amount, _ in rows])
    return LogisticRegression(C=1.0, max_iter=2000).fit(X, labels)


def train(rounds: list[list[tuple]], calibration: list[list[tuple]], costs, target_fpr: float,
          version: int, min_rounds: int = MIN_ROUNDS) -> StackedCombiner | None:
    """`rounds`: per round, (votes as cast, amount_cents, is_fraud) for each labeled payment, oldest
    first. `calibration`: per round, (votes, amount_cents) for recent honest payments, re-scored by the
    defense about to be deployed. Returns None until there's enough data (the orchestrator then keeps
    its noisy-OR), or when no shift keeps the calibration rounds under the target."""
    rounds = [r for r in rounds if r][-WINDOW_ROUNDS:]
    calibration = [c for c in calibration if c]
    if len(rounds) < min_rounds or not calibration:
        return None
    voters = sorted({v.voter for rnd in rounds for votes, _, _ in rnd for v in votes})
    model = _fit(voters, [x for rnd in rounds for x in rnd])
    if model is None:
        return None
    scored = []
    for rnd in calibration:
        X = np.array([StackedCombiner.row(voters, votes, amount) for votes, amount in rnd])
        scored.append(list(zip(model.predict_proba(X)[:, 1], (amount for _, amount in rnd))))
    for s in SHIFTS:  # judged on its worst calibration round (or all of them pooled, with POOLED_CALIBRATION)
        flagged = [sum(_action(decision_costs(costs), _shifted(float(p), s), amount) != Action.ALLOW for p, amount in rnd)
                   for rnd in scored]
        rate = (sum(flagged) / sum(len(rnd) for rnd in scored) if POOLED_CALIBRATION
                else max(f / len(rnd) for f, rnd in zip(flagged, scored)))
        if rate <= target_fpr:
            return StackedCombiner(voters, model, s, version)
    return None


def _action(costs, p: float, amount_cents: int) -> Action:
    expected = costs.expected_costs(p, amount_cents)
    return min(expected, key=expected.get)

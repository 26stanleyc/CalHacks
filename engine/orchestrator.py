"""Orchestrator (spec 3.3, 3.4): combines the voters, applies hard vetoes, and picks the action
with the lowest expected cost under the cost matrix.

Combined suspicion p is a weighted noisy-OR of voter scores, 1 - prod(1 - w_i * s_i), with
weights scaled so the largest is 1. One confident lane can carry a decision; weak signals from
several lanes add up. Hedge updates the weights in S4. Once there's enough labeled history, a learned
combiner (engine/learning/stacking.py) replaces the noisy-OR with a calibrated probability.
"""

from dataclasses import dataclass, field

from contracts.schemas import Action, Decision, FeatureVector, Transaction, Vote

ESCALATION_BAND = 0.25  # relative cost gap under which a call counts as close (used by S5)
# Tuning knobs (engine/tune.py), decision-only: the simulator and the scorer keep the real CostMatrix.
SPLIT_BLOCK = False  # choose step-up vs block from the combiner's probability before its false-positive shift
SPLIT_BLOCK_AT = 0.0  # with SPLIT_BLOCK: block only when that probability is at least this (else step up)
DECISION_COSTS = None  # a CostMatrix the defense decides with, in place of `costs`
VOTER_FLOOR_UNTIL = 0  # while the combiner is at most this version (1 = the round-2 bootstrap), never act softer
#                        than TagTeam's own XGBoost voter's verdict
STRICTNESS = {Action.ALLOW: 0, Action.STEP_UP: 1, Action.BLOCK: 2}


@dataclass(frozen=True)
class CostMatrix:
    """All costs in cents. A missed fraud costs its amount; a false block loses the sale's margin
    plus the customer's goodwill; a step-up costs friction and some honest shoppers walk away,
    while some fraudsters get through it."""

    false_block_margin: float = 0.30
    false_block_fixed_cents: float = 300
    legit_step_up_abandon: float = 0.25
    step_up_friction_cents: float = 150
    fraud_step_up_pass: float = 0.20

    def false_block_cost(self, amount_cents: int) -> float:
        return self.false_block_margin * amount_cents + self.false_block_fixed_cents

    def expected_costs(self, p: float, amount_cents: int) -> dict[Action, float]:
        lost_sale = self.false_block_cost(amount_cents)
        return {
            Action.ALLOW: p * amount_cents,
            Action.STEP_UP: (1 - p) * (self.legit_step_up_abandon * lost_sale + self.step_up_friction_cents)
            + p * self.fraud_step_up_pass * amount_cents,
            Action.BLOCK: (1 - p) * lost_sale,
        }


def decision_costs(costs: "CostMatrix") -> "CostMatrix":
    """The cost matrix the defense decides with (DECISION_COSTS when tuning sets it)."""
    return DECISION_COSTS or costs


def hard_veto(features: FeatureVector) -> str | None:
    """Clear-cut cases that are blocked whatever the scores say."""
    v = features.values
    if v.get("i_identity_status", 0.0) >= 2.0:
        return "identity:failed"
    if v.get("m_over_cap_ratio", 0.0) > 1.0:
        return "mandate:over_cap"
    if v.get("m_daily_spend_ratio", 0.0) > 1.0:
        return "mandate:over_daily_cap"
    if v.get("m_merchant_allowed", 1.0) == 0.0:
        return "mandate:forbidden_merchant"
    if v.get("m_category_allowed", 1.0) == 0.0:
        return "mandate:forbidden_category"
    return None


VETO_TEXT = {
    "identity:failed": "the person behind this agent failed identity checks",
    "mandate:over_cap": "it's over the human's per-payment limit",
    "mandate:over_daily_cap": "it would exceed the human's daily spending limit",
    "mandate:forbidden_merchant": "the human didn't authorize this merchant",
    "mandate:forbidden_category": "the human didn't authorize this kind of purchase",
}


@dataclass
class Orchestrator:
    costs: CostMatrix = field(default_factory=CostMatrix)
    weights: dict[str, float] = field(default_factory=dict)  # voter -> weight; missing voters weigh 1.0
    combiner: object | None = None  # the learned combiner (engine/learning/stacking.py), once trained

    def probability(self, tx: Transaction, votes: list[Vote]) -> float:
        """The fraud probability the cost matrix acts on: the learned combiner's once it exists,
        the noisy-OR until then."""
        if self.combiner is not None:
            return self.combiner.probability(votes, tx.amount_cents)
        return self.combined_score(votes)

    def combined_score(self, votes: list[Vote]) -> float:
        raw = {v.voter: self.weights.get(v.voter, 1.0) for v in votes}
        top = max(raw.values(), default=1.0) or 1.0
        miss = 1.0
        for v in votes:
            miss *= 1.0 - (raw[v.voter] / top) * v.score
        return 1.0 - miss

    def decide(self, tx: Transaction, votes: list[Vote], features: FeatureVector) -> Decision:
        p = self.probability(tx, votes)
        veto = hard_veto(features)
        matrix = decision_costs(self.costs)
        costs = matrix.expected_costs(p, tx.amount_cents)
        action = Action.BLOCK if veto else min(costs, key=costs.get)
        if SPLIT_BLOCK and not veto and action != Action.ALLOW and self.combiner is not None:
            # the shift is for the false-positive cap, which only bounds allow vs flag
            p_raw = self.combiner.probability(votes, tx.amount_cents, shifted=False)
            raw = matrix.expected_costs(p_raw, tx.amount_cents)
            action = Action.BLOCK if raw[Action.BLOCK] < raw[Action.STEP_UP] and p_raw >= SPLIT_BLOCK_AT else Action.STEP_UP
        if not veto and self.combiner is not None and self.combiner.version <= VOTER_FLOOR_UNTIL:
            own = next((v.vote for v in votes if v.voter == "xgboost"), Action.ALLOW)
            action = max(action, own, key=STRICTNESS.get)
        return Decision(
            tx_id=tx.tx_id, action=action, weighted_score=1.0 if veto else round(p, 6), votes=votes,
            weights={v.voter: self.weights.get(v.voter, 1.0) for v in votes},
            vetoed_by=veto, escalated=False, explanation=_explain(action, veto, votes),
        )

    def is_close_call(self, p: float, amount_cents: int) -> bool:
        """True when the best and second-best actions cost about the same (S5 escalates these)."""
        best, second = sorted(decision_costs(self.costs).expected_costs(p, amount_cents).values())[:2]
        return second - best <= ESCALATION_BAND * max(second, 1.0)


def _explain(action: Action, veto: str | None, votes: list[Vote]) -> str:
    if veto:
        return f"Blocked because {VETO_TEXT.get(veto, veto)}."
    fired = [r for v in sorted(votes, key=lambda v: -v.score) for r in v.rule_ids]
    if action == Action.ALLOW:
        return "Allowed: no strong fraud signals." if not fired else f"Allowed: only weak signals ({', '.join(fired)})."
    verb = "Blocked" if action == Action.BLOCK else "Extra check required"
    return f"{verb}: {', '.join(fired)}."

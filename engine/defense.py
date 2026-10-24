"""Placeholder scoring for S1. S2 replaces these with the feature service, the rule DSL and the
cost-matrix orchestrator; the agent wiring in bureau.py stays the same."""

from contracts.schemas import Action, Decision, FeatureVector, Lane, Transaction, Vote

S1_LANES = (Lane.TIMING, Lane.GRAPH, Lane.IDENTITY, Lane.VELOCITY)  # mandate joins in M9

BLOCK_AT = 0.8
STEP_UP_AT = 0.5


def placeholder_features(tx: Transaction) -> FeatureVector:
    return FeatureVector(tx_id=tx.tx_id, values={"amount_cents": float(tx.amount_cents)})


def placeholder_vote(lane: Lane, tx: Transaction, features: FeatureVector) -> Vote:
    return Vote(tx_id=tx.tx_id, voter=lane.value, score=0.1, vote=Action.ALLOW, rule_ids=[f"{lane.value}.placeholder"])


def placeholder_decide(tx: Transaction, votes: list[Vote]) -> Decision:
    weights = {v.voter: 1.0 for v in votes}
    score = sum(v.score * weights[v.voter] for v in votes) / max(1.0, sum(weights.values()))
    action = Action.BLOCK if score >= BLOCK_AT else Action.STEP_UP if score >= STEP_UP_AT else Action.ALLOW
    return Decision(tx_id=tx.tx_id, action=action, weighted_score=score, votes=votes, weights=weights,
                    explanation="S1 placeholder: equal-weight average of placeholder votes.")

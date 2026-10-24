"""Specialists (spec 3.3): each owns one lane's rulebook and scores a transaction from its lane's
feature slice only. Scoring is plain code, so it takes microseconds; the specialist's LLM is used
later for reflection and spawning (S4, S6), never on this path."""

from contracts.schemas import Action, FeatureVector, Lane, Transaction, Vote
from engine.dsl import Rulebook, evaluate, validate_rulebook
from engine.features import FeatureCatalog

VOTE_BLOCK_AT = 0.6
VOTE_STEP_UP_AT = 0.15


class Specialist:
    def __init__(self, lane: Lane, rulebook: Rulebook, catalog: FeatureCatalog, validate: bool = True):
        if rulebook.lane != lane:
            raise ValueError(f"{lane.value} specialist given the {rulebook.lane.value} rulebook")
        if validate:
            validate_rulebook(rulebook, catalog)
        self.lane = lane
        self.rulebook = rulebook
        self.features = set(catalog.lane_features(lane))

    def vote(self, tx: Transaction, features: FeatureVector) -> Vote:
        values = {k: v for k, v in features.values.items() if k in self.features}  # lane slice only
        score, fired = evaluate(self.rulebook, values)
        action = Action.BLOCK if score >= VOTE_BLOCK_AT else Action.STEP_UP if score >= VOTE_STEP_UP_AT else Action.ALLOW
        rationale = "; ".join(r.description for r in self.rulebook.rules if r.id in fired) or None
        return Vote(tx_id=tx.tx_id, voter=self.lane.value, score=round(score, 6), vote=action, rule_ids=fired,
                    rationale=rationale)

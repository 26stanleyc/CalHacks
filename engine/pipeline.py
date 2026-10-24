"""The decision path (spec 3.4) as plain function calls: features -> specialists -> orchestrator.

The Bureau's agents call these same objects, so a decision made through uAgents messaging is
identical to one made in-process (the in-process path is what benchmarks and tests use).
"""

import hashlib
import json

from contracts.interfaces import Voter
from contracts.schemas import Action, Decision, FeatureVector, IdentityStatus, Lane, Transaction, TrafficSource, Vote
from engine.dsl import Rulebook, load_seed_rulebooks
from engine.features import FeatureCatalog, FeatureService
from engine.orchestrator import Orchestrator
from engine.specialists import Specialist

S2_LANES = (Lane.TIMING, Lane.GRAPH, Lane.IDENTITY, Lane.VELOCITY)
ALL_LANES = (*S2_LANES, Lane.MANDATE)  # the mandate specialist joined in M9


class DefensePipeline:
    def __init__(self, lanes: tuple[Lane, ...] = ALL_LANES, catalog: FeatureCatalog | None = None,
                 rulebooks: dict[Lane, Rulebook] | None = None, identities: dict[str, IdentityStatus] | None = None,
                 orchestrator: Orchestrator | None = None, voters: list[Voter] | None = None,
                 product_text: dict[str, str] | None = None):
        self.lanes = lanes
        self.catalog = catalog or FeatureCatalog()
        self.features = FeatureService(self.catalog, identities, product_text)
        rulebooks = rulebooks or load_seed_rulebooks(self.catalog, lanes)
        self.specialists = {lane: Specialist(lane, rulebooks[lane], self.catalog) for lane in lanes}
        self.orchestrator = orchestrator or Orchestrator()
        self.voters = list(voters or [])  # e.g. Ryan's XGBoost voter: one more vote, weighed like any other

    def featurize(self, tx: Transaction, commit: bool | None = None) -> FeatureVector:
        """`commit=None` remembers the payment unless it was chat-submitted: those are scored for
        real but must never change the defense's state (spec 3.7)."""
        if commit is None:
            commit = tx.source != TrafficSource.ASI_ONE
        return self.features.compute(tx, commit=commit and tx.source != TrafficSource.ASI_ONE)

    def lane_slice(self, lane: Lane, features: FeatureVector) -> FeatureVector:
        return features.slice(self.catalog.lane_features(lane))

    def vote(self, lane: Lane, tx: Transaction, features: FeatureVector) -> Vote:
        return self.specialists[lane].vote(tx, self.lane_slice(lane, features))

    def combine(self, tx: Transaction, votes: list[Vote], features: FeatureVector) -> Decision:
        """The orchestrator's decision. Extra voters vote here (so the Bureau path gets them too),
        unless their vote is already in `votes` (a replay of a logged decision)."""
        present = {v.voter for v in votes}
        votes = [*votes, *(voter.score(tx, features) for voter in self.voters if voter.name not in present)]
        order = {lane.value: i for i, lane in enumerate(self.lanes)}
        decision = self.orchestrator.decide(tx, sorted(votes, key=lambda v: order.get(v.voter, 99)), features)
        if decision.action == Action.BLOCK and not decision.vetoed_by:  # a watch rule never blocks on its own:
            watch = {r.id for s in self.specialists.values() for r in s.rulebook.rules if r.watch}  # drop the lanes
            if any(set(v.rule_ids) & watch for v in decision.votes):  # that fired only watch rules and decide again
                without = [v.model_copy(update={"score": 0.0, "vote": Action.ALLOW, "rule_ids": []})
                           if v.rule_ids and set(v.rule_ids) <= watch else v for v in decision.votes]
                if self.orchestrator.decide(tx, without, features).action != Action.BLOCK:
                    decision = decision.model_copy(update={"action": Action.STEP_UP})
        return decision

    def fingerprint(self) -> str:
        """A hash of everything learning can change: rulebooks, weights and the feature catalog."""
        state = {"rules": {lane.value: s.rulebook.model_dump(mode="json") for lane, s in self.specialists.items()},
                 "weights": self.orchestrator.weights, "features": sorted(self.catalog.lanes),
                 "voters": [getattr(v, "version", None) for v in self.voters]}
        return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()

    def score(self, tx: Transaction, features: FeatureVector) -> Decision:
        """Decide from features computed elsewhere (benchmarks give every contender the same ones)."""
        return self.combine(tx, [self.vote(lane, tx, features) for lane in self.lanes], features)

    def decide(self, tx: Transaction, commit: bool | None = None) -> tuple[Decision, FeatureVector]:
        features = self.featurize(tx, commit)
        return self.score(tx, features), features

"""The decision path (spec 3.4) as plain function calls: features -> specialists -> orchestrator.

The Bureau's agents call these same objects, so a decision made through uAgents messaging is
identical to one made in-process (the in-process path is what benchmarks and tests use).
"""

from contracts.schemas import Decision, FeatureVector, IdentityStatus, Lane, Transaction, TrafficSource, Vote
from engine.dsl import Rulebook, load_seed_rulebooks
from engine.features import FeatureCatalog, FeatureService
from engine.orchestrator import Orchestrator
from engine.specialists import Specialist

S2_LANES = (Lane.TIMING, Lane.GRAPH, Lane.IDENTITY, Lane.VELOCITY)  # mandate specialist joins in M9


class DefensePipeline:
    def __init__(self, lanes: tuple[Lane, ...] = S2_LANES, catalog: FeatureCatalog | None = None,
                 rulebooks: dict[Lane, Rulebook] | None = None, identities: dict[str, IdentityStatus] | None = None,
                 orchestrator: Orchestrator | None = None):
        self.lanes = lanes
        self.catalog = catalog or FeatureCatalog()
        self.features = FeatureService(self.catalog, identities)
        rulebooks = rulebooks or load_seed_rulebooks(self.catalog, lanes)
        self.specialists = {lane: Specialist(lane, rulebooks[lane], self.catalog) for lane in lanes}
        self.orchestrator = orchestrator or Orchestrator()

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
        order = {lane.value: i for i, lane in enumerate(self.lanes)}
        return self.orchestrator.decide(tx, sorted(votes, key=lambda v: order.get(v.voter, 99)), features)

    def score(self, tx: Transaction, features: FeatureVector) -> Decision:
        """Decide from features computed elsewhere (benchmarks give every contender the same ones)."""
        return self.combine(tx, [self.vote(lane, tx, features) for lane in self.lanes], features)

    def decide(self, tx: Transaction, commit: bool | None = None) -> tuple[Decision, FeatureVector]:
        features = self.featurize(tx, commit)
        return self.score(tx, features), features

"""The graph specialist's pass-through signal (TagTeam only; tuning experiment, engine/tune.py "passthrough").

A mule passes stolen money straight on: money arrives, and the same amount leaves within the hour. On
PaySim a takeover is a transfer followed by a cash-out of exactly the same amount in the same hour, from
two different (anonymised) accounts. g_passthrough_1h is 1 when a payment of exactly this amount, in the
opposite direction (transfer <-> cash_out) and from a different agent, was seen in the last hour.

It is computed from observable traffic only (no labels), for TagTeam's own pipeline: the feature catalog
it extends belongs to TagTeam (its live pipeline, staging copies and arena sandbox share it), and the
XGBoost baseline builds its own catalog, so it never sees the feature. Only the graph lane reads it:
TagTeam's own XGBoost and anomaly voters are given features without it, so any gain is the specialist's.
Disclosed in the demo: XGBoost could use the same input if it were given it.
"""

from collections import deque

from contracts.schemas import Lane
from engine import features as F
from engine.dsl import Rule
from engine.specialists import Specialist

FEATURE = "g_passthrough_1h"
PAIR = {"transfer": "cash_out", "cash_out": "transfer"}
WINDOW_S = 3600
RULE = Rule(id="graph.pass_through", score=0.85, when={"feature": FEATURE, "op": ">=", "value": 1},
            description="Money in and straight out: a payment of exactly this amount in the opposite direction "
                        "within the hour (a mule passing stolen funds on).")


def _patch_features() -> None:
    if getattr(F.FeatureService, "_passthrough", False):
        return
    F.FeatureService._passthrough = True
    compute = F.FeatureService.compute

    def with_passthrough(self, tx, commit=True):
        fv = compute(self, tx, commit)
        if not getattr(self.catalog, "_passthrough", False):
            return fv
        recent = self.__dict__.setdefault("_passthrough_recent", deque())
        while recent and tx.ts - recent[0][0] >= WINDOW_S:
            recent.popleft()
        other = PAIR.get(tx.category)
        fv.values[FEATURE] = float(other is not None and any(
            amount == tx.amount_cents and category == other and agent != tx.agent_id for _, amount, category, agent in recent))
        if commit:
            recent.append((tx.ts, tx.amount_cents, tx.category, tx.agent_id))
        return fv

    F.FeatureService.compute = with_passthrough


def _blind(cls):
    """A voter class that never sees the pass-through feature, in scoring or in training."""
    def strip(fv):
        return fv.model_copy(update={"values": {k: v for k, v in fv.values.items() if k != FEATURE}})

    class Blind(cls):
        def score(self, tx, features):
            return super().score(tx, strip(features))

        def retrain(self, examples):
            return super().retrain([(strip(fv), y) for fv, y in examples])

    Blind.__name__ = f"Blind{cls.__name__}"
    return Blind


def apply(engine) -> None:
    """Give TagTeam's graph specialist the pass-through feature and its seed rule."""
    _patch_features()
    catalog = engine.pipeline.catalog
    catalog._passthrough = True
    catalog.lanes[FEATURE] = Lane.GRAPH
    F.SUSPICIOUS_WHEN[FEATURE] = "high"
    graph = engine.pipeline.specialists[Lane.GRAPH]
    book = graph.rulebook.model_copy(update={"rules": [*graph.rulebook.rules, RULE]})
    engine.pipeline.specialists[Lane.GRAPH] = Specialist(Lane.GRAPH, book, catalog)
    engine.learning.seed_ids |= {RULE.id}
    for voter in engine.pipeline.voters:
        voter.__class__ = _blind(type(voter))
    for lane, specialist in engine.pipeline.specialists.items():
        specialist.features = set(catalog.lane_features(lane))

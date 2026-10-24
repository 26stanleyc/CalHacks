"""Replaying rules on logged traffic: what a rule catches, what it costs honest shoppers, and what
the whole defense's false-positive rate would be with it deployed. Rules are code, so replay takes
milliseconds and no LLM calls (spec 3.3 spot-check, 4.8 vetoes).

Every replay scores the defense as it stands now: fresh votes from every lane's current rules and
fresh votes from the extra voters (the XGBoost voter), never the votes logged when a payment was
decided, since rules and models have changed since.
"""

from dataclasses import dataclass

from contracts.schemas import Action, Decision, Lane
from engine.dsl import Rule, Rulebook, holds
from engine.learning.labels import Record
from engine.pipeline import DefensePipeline
from engine.specialists import Specialist


class VoterVotes:
    """The extra voters' fresh votes, scored once per learning round: their models don't change
    until the retrain at its end."""

    def __init__(self, pipeline: DefensePipeline):
        self.pipeline = pipeline
        self._votes: dict[str, list] = {}

    def get(self, r: Record) -> list:
        if r.tx.tx_id not in self._votes:
            self._votes[r.tx.tx_id] = [voter.score(r.tx, r.features) for voter in self.pipeline.voters]
        return self._votes[r.tx.tx_id]


def decide_now(pipeline: DefensePipeline, r: Record, voters: VoterVotes, lane: Lane | None = None,
               specialist: Specialist | None = None) -> Decision:
    """What the defense would decide on this payment now, optionally with one lane's specialist swapped."""
    votes = [specialist.vote(r.tx, pipeline.lane_slice(l, r.features)) if l == lane and specialist is not None
             else pipeline.vote(l, r.tx, r.features) for l in pipeline.lanes]
    return pipeline.combine(r.tx, [*votes, *voters.get(r)], r.features)


@dataclass
class RuleReplay:
    fires_legit: int  # honest payments (inside their mandate) it fires on
    legit: int
    fires_fraud: int
    newly_caught: int  # fraud it would catch that the defense as it stands lets through
    fires_share: float  # share of all payments it fires on
    system_fp_rate: float  # the whole defense's false-positive rate with this rule added

    @property
    def legit_rate(self) -> float:
        return self.fires_legit / max(1, self.legit)

    def summary(self) -> dict:
        return {"fires_on_legit": self.fires_legit, "legit_payments": self.legit, "fires_on_fraud": self.fires_fraud,
                "newly_caught_misses": self.newly_caught, "fires_on_share_of_all": round(self.fires_share, 4),
                "system_false_positive_rate_with_rule": round(self.system_fp_rate, 4)}


def honest(records: list[Record]) -> list[Record]:
    return [r for r in records if not r.truth.is_fraud and not r.truth.breaches_mandate]


def fp_rate(pipeline: DefensePipeline, records: list[Record], voters: VoterVotes, lane: Lane | None = None,
            rulebook: Rulebook | None = None) -> float:
    """The defense's false-positive rate on these records as it stands now, optionally with one
    lane's rulebook swapped (validation skipped: the caller checks the cap)."""
    specialist = Specialist(lane, rulebook, pipeline.catalog, validate=False) if rulebook is not None else None
    legit = honest(records)
    flagged = sum(decide_now(pipeline, r, voters, lane, specialist).action != Action.ALLOW for r in legit)
    return flagged / max(1, len(legit))


def replay_rule(rule: Rule, lane: Lane, pipeline: DefensePipeline, records: list[Record], voters: VoterVotes) -> RuleReplay:
    names = set(pipeline.catalog.lane_features(lane))
    fires_legit = legit = fires_fraud = newly = fired_any = 0
    for r in records:
        values = {k: v for k, v in r.features.values.items() if k in names}
        fires = holds(rule.when, values)
        fired_any += fires
        if r.truth.is_fraud:
            fires_fraud += fires
            newly += fires and decide_now(pipeline, r, voters).action == Action.ALLOW
        elif not r.truth.breaches_mandate:
            legit += 1
            fires_legit += fires
    current = pipeline.specialists[lane].rulebook
    with_rule = current.model_copy(update={"rules": [*current.rules, rule]})
    return RuleReplay(fires_legit, legit, fires_fraud, newly, fired_any / max(1, len(records)),
                      fp_rate(pipeline, records, voters, lane, with_rule))

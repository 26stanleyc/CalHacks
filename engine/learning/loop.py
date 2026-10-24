"""One round of learning (spec 4 steps 5-9; M4). Runs after the round's waves, never between them,
and not at all while the defense is frozen:

1. The critic assigns each miss to a lane, or marks it uncovered; every assignment is spot-checked.
2. Rules with no true hits for RETIRE_AFTER rounds are retired.
3. Each specialist reflects on its assigned misses and its own false alarms, and proposes rules.
   Every proposed rule is validated against its lane, replayed on recent traffic, and reviewed by
   the critic. The rule cap holds, and the false-positive cap is enforced in code as well.
4. Hedge updates the orchestrator's voter weights, and extra voters (XGBoost) retrain on all
   labels so far.

Learning works on a staging copy of the defense and commits it in one step at the end, under the
engine's state lock, so a check made while the defense learns never sees half a change.

Everything is logged: critic checks, reflections, rulebook versions with diffs, and vetoes with
their reasons.
"""

import contextlib
import copy
import logging
from collections import deque
from dataclasses import dataclass, field

from contracts.schemas import Lane, TrafficSource
from engine.brains import Brain
from engine.dsl import MAX_RULES, Rule, holds, validate_rule
from engine.learning import hedge
from engine.learning.critic import Critic
from engine.learning.labels import UNCOVERED, LegitReference, Record
from engine.learning.reflection import INSTRUCTIONS, MAX_RULES_PER_REFLECTION, ReflectionProposal
from engine.learning.replay import VoterVotes, fp_rate, replay_rule
from engine.orchestrator import Orchestrator
from engine.pipeline import DefensePipeline
from engine.specialists import Specialist
from engine.store import LogStore

RETIRE_AFTER = 3  # rounds without a true hit
GUARD_AT = 0.5  # of the FP cap: above this, the rules causing false alarms come out (headroom for drift)
VOTER_AT = 0.3  # of the FP cap: what a retrained voter is calibrated to, out of sample
REVET_HONEST_SHARE = 0.003  # a learned rule firing on more of the growing honest record than this retires
REPLAY_ROUNDS = 3  # rounds of fraud a proposed rule is replayed on (what it would catch)
HONEST_ROUNDS = 10  # rounds of honest traffic it's replayed on (false alarms need far more data than catches)
MISSES_PER_REFLECTION = 30
FALSE_ALARMS_PER_REFLECTION = 20
MAX_REMOVALS_PER_REFLECTION = 2

log = logging.getLogger(__name__)


@dataclass
class RoundLearning:
    """What one round of learning did, for metrics and tests."""

    assignments: dict[str, str] = field(default_factory=dict)
    uncovered: int = 0
    deployed: list[str] = field(default_factory=list)
    vetoed: list[tuple[str, str]] = field(default_factory=list)  # (rule id, reason)
    retired: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    critic_agreement: float | None = None
    weights: dict[str, float] = field(default_factory=dict)


class LearningLoop:
    def __init__(self, pipeline: DefensePipeline, brain: Brain, store: LogStore, run_id: str, fp_cap: float = 0.02,
                 lock: contextlib.AbstractContextManager | None = None):
        self.lock = lock or contextlib.nullcontext()  # held only while the live defense changes
        self.live = pipeline
        self.pipeline = pipeline  # during learn(): the staging copy
        self.seed_ids = {r.id for s in pipeline.specialists.values() for r in s.rulebook.rules}
        self.brain = brain
        self.store = store
        self.run_id = run_id
        self.fp_cap = fp_cap
        self.critic = Critic(brain, pipeline.lanes, fp_cap)
        self.recent: deque[list[Record]] = deque(maxlen=REPLAY_ROUNDS)
        self.honest: deque[list[Record]] = deque(maxlen=HONEST_ROUNDS)
        self.last_true_hit: dict[str, int] = {}  # rule id -> last round it fired on fraud
        self.example_rounds: list[list[tuple]] = []  # (features, is_fraud) per round, every training label so far
        self.hedge_weights: dict[str, float] = dict(pipeline.orchestrator.weights)  # Hedge's own state
        self.voter_caps: dict[str, float] = {}  # the most weight each extra voter has earned, out of sample
        self._closest: dict[str, str] = {}  # uncovered miss -> the lane whose features come closest

    # --- the round ---------------------------------------------------------------------------

    def learn(self, round_no: int, records: list[Record]) -> RoundLearning:
        self.pipeline = copy.copy(self.live)  # staging: the same features and voters, its own rulebooks and weights
        self.pipeline.specialists = dict(self.live.specialists)
        self.pipeline.orchestrator = Orchestrator(costs=self.live.orchestrator.costs,
                                                  weights=dict(self.live.orchestrator.weights))
        self.voter_votes = VoterVotes(self.pipeline)
        try:
            return self._learn(round_no, records)
        finally:
            self.pipeline = self.live

    def _learn(self, round_no: int, records: list[Record]) -> RoundLearning:
        records = [r for r in records if r.tx.source != TrafficSource.ASI_ONE]
        self.recent.append([r for r in records if r.truth.is_fraud])
        self.honest.append([r for r in records if not r.truth.is_fraud])
        replayed = [r for rnd in self.recent for r in rnd] + [r for rnd in self.honest for r in rnd]
        reference = LegitReference(replayed)
        lane_features = {lane: self.pipeline.catalog.lane_features(lane) for lane in self.pipeline.lanes}
        out = RoundLearning()
        self.critic.start_round()

        misses = [r for r in records if r.missed or r.code_missed]
        out.assignments = self.critic.assign(misses, lane_features, reference)
        out.uncovered = sum(lane == UNCOVERED for lane in out.assignments.values())
        # Misses that fit no lane go to the closest specialist anyway, as a prompt to find a signal (spec 4.5).
        self._closest = {m.tx.tx_id: max(lane_features, key=lambda l: reference.separation(m.features.values, lane_features[l])).value
                         for m in misses if out.assignments.get(m.tx.tx_id) == UNCOVERED}

        stats = self._rule_stats(round_no, records)
        out.retired = self._retire(round_no)
        out.removed += self._revet(round_no)
        out.removed += self._guard(round_no, records, stats)
        for lane in self.pipeline.lanes:
            self._reflect(round_no, lane, records, replayed, reference, out, stats)

        out.critic_agreement = self.critic.end_round()
        self.store.log_rows("critic_checks", [
            {"run_id": self.run_id, "round": round_no, "kind": c.kind, "critic": c.critic, "automated": c.automated,
             "agree": c.agree, "second_opinion": c.second_opinion} for c in self.critic.checks])

        self.hedge_weights = hedge.update(self.hedge_weights, records, self.pipeline.orchestrator.costs)
        self.example_rounds.append([(r.features, r.truth.is_fraud) for r in records])
        self.voter_caps = self._calibrate_voters(self.hedge_weights, records)
        weights = {voter: min(w, self.voter_caps.get(voter, 1.0)) for voter, w in self.hedge_weights.items()}
        examples = [e for rnd in self.example_rounds for e in rnd]
        with self.lock:  # commit everything at once
            self.live.specialists = dict(self.pipeline.specialists)
            self.live.orchestrator.weights = weights
            for voter in self.live.voters:
                voter.retrain(examples)
        out.weights = dict(weights)
        return out

    def _calibrate_voters(self, weights: dict[str, float], latest: list[Record]) -> dict[str, float]:
        """Each extra voter's cap, vetted like a rule but out of sample: a shadow copy trained on every
        round but the latest is replayed on the latest round's honest payments, and the cap is the
        largest weight (up to 1) that keeps the defense under VOTER_AT of the FP cap there. With no
        earlier round to train on, a voter is capped at the floor."""
        caps = {}
        honest = [r for r in latest if not r.truth.is_fraud and not r.truth.breaches_mandate]
        target = self.fp_cap * VOTER_AT
        for voter in self.pipeline.voters:
            if len(self.example_rounds) < 2 or not honest:
                caps[voter.name] = hedge.FLOOR
                continue
            shadow = copy.deepcopy(voter)
            shadow.retrain([e for rnd in self.example_rounds[:-1] for e in rnd])
            cap = 1.0
            while cap > hedge.FLOOR and self._shadow_fp(shadow, {**weights, voter.name: cap}, honest) > target:
                cap = max(hedge.FLOOR, cap * 0.7)
            caps[voter.name] = round(cap, 6)
        return caps

    def _shadow_fp(self, shadow, weights: dict[str, float], honest: list[Record]) -> float:
        orchestrator = Orchestrator(costs=self.pipeline.orchestrator.costs, weights=weights)
        flagged = 0
        for r in honest:
            votes = [self.pipeline.vote(lane, r.tx, r.features) for lane in self.pipeline.lanes]
            votes.append(shadow.score(r.tx, r.features))
            flagged += orchestrator.decide(r.tx, votes, r.features).action.value != "allow"
        return flagged / len(honest)

    # --- rulebook hygiene ----------------------------------------------------------------------

    def _rule_stats(self, round_no: int, records: list[Record]) -> dict[str, dict[str, int]]:
        """Per rule this round: how often it fired on fraud and on honest payments."""
        stats: dict[str, dict[str, int]] = {}
        for lane, specialist in self.pipeline.specialists.items():
            for rule in specialist.rulebook.rules:
                stats[rule.id] = {"true_hits": 0, "false_hits": 0}
                self.last_true_hit.setdefault(rule.id, round_no - 1)
        for r in records:
            for vote in r.decision.votes:
                for rule_id in vote.rule_ids:
                    if rule_id not in stats:
                        continue
                    if r.truth.is_fraud:
                        stats[rule_id]["true_hits"] += 1
                        self.last_true_hit[rule_id] = round_no
                    elif not r.truth.breaches_mandate:
                        stats[rule_id]["false_hits"] += 1
        return stats

    def _retire(self, round_no: int) -> list[str]:
        retired = []
        for lane, specialist in self.pipeline.specialists.items():
            keep = [r for r in specialist.rulebook.rules if round_no - self.last_true_hit.get(r.id, round_no) < RETIRE_AFTER]
            gone = [r.id for r in specialist.rulebook.rules if r not in keep]
            if gone:
                self._deploy(round_no, lane, keep, {"retired": gone}, None)
                retired += gone
        return retired

    def _revet(self, round_no: int) -> list[str]:
        """Learned rules are vetted again every round against the growing honest record: a threshold
        that looked rare on two rounds of data can turn out to be common on ten. Seed rules aren't."""
        honest = [r for rnd in self.honest for r in rnd if not r.truth.breaches_mandate]
        removed = []
        for lane, specialist in list(self.pipeline.specialists.items()):
            names = set(self.pipeline.catalog.lane_features(lane))
            keep, gone = [], []
            for rule in specialist.rulebook.rules:
                fires = sum(holds(rule.when, {k: v for k, v in r.features.values.items() if k in names}) for r in honest)
                learned = rule.id not in self.seed_ids
                (gone if learned and honest and fires / len(honest) > REVET_HONEST_SHARE else keep).append(rule)
            if gone:
                self._deploy(round_no, lane, keep, {"revet_removed": [r.id for r in gone]}, None)
                removed += [r.id for r in gone]
        return removed

    def _guard(self, round_no: int, records: list[Record], stats: dict) -> list[str]:
        """The false-positive cap holds for rules already deployed too: traffic drifts, so when this
        round's traffic, replayed on the defense as it stands, runs above GUARD_AT of the cap, the
        rules with the most false alarms come out until it doesn't (or no rule is left to blame)."""
        target = self.fp_cap * GUARD_AT
        removed = []
        while fp_rate(self.pipeline, records, self.voter_votes) > target:
            lane_of = {r.id: lane for lane, s in self.pipeline.specialists.items() for r in s.rulebook.rules}
            offenders = sorted((rid for rid, s in stats.items() if s["false_hits"] > 0 and rid in lane_of),
                               key=lambda rid: (-stats[rid]["false_hits"], stats[rid]["true_hits"], rid))
            if not offenders:
                break  # the excess isn't from rules (weights or the voter); calibration handles those
            rule_id = offenders[0]
            lane = lane_of[rule_id]
            rules = [r for r in self.pipeline.specialists[lane].rulebook.rules if r.id != rule_id]
            self._deploy(round_no, lane, rules, {"fp_guard_removed": [rule_id]}, None)
            removed.append(rule_id)
        return removed

    def _deploy(self, round_no: int, lane: Lane, rules: list[Rule], diff: dict, veto: str | None) -> None:
        """Change the staging copy (committed at the end of learn) and log the version."""
        book = self.pipeline.specialists[lane].rulebook
        if veto is None:
            new = book.model_copy(update={"rules": rules, "version": book.version + 1})
            self.pipeline.specialists[lane] = Specialist(lane, new, self.pipeline.catalog)
            book = new
        self.store.log_rows("rulebook_versions", [{
            "run_id": self.run_id, "round": round_no, "lane": lane.value, "draft": 0,
            "rules": [r.model_dump(mode="json") for r in book.rules], "diff": diff, "deployed": veto is None,
            "veto_reason": veto}])

    # --- reflection -----------------------------------------------------------------------------

    def _reflect(self, round_no: int, lane: Lane, records: list[Record], replayed: list[Record],
                 reference: LegitReference, out: RoundLearning, stats: dict) -> None:
        names = self.pipeline.catalog.lane_features(lane)
        mine = [r for r in records if (r.missed or r.code_missed) and (out.assignments.get(r.tx.tx_id) == lane.value
                                                     or self._closest.get(r.tx.tx_id) == lane.value)]
        false_alarms = [r for r in records if r.false_positive
                        and any(v.voter == lane.value and v.rule_ids for v in r.decision.votes)]
        book = self.pipeline.specialists[lane].rulebook
        room = MAX_RULES - len(book.rules)

        def slice_of(r: Record) -> dict:
            return {n: r.features.values[n] for n in names if n in r.features.values}

        def described(r: Record) -> dict:
            """Each feature with how rare it is among honest payments, on either side."""
            return {n: {"value": v, "honest_at_or_above": round(reference.share_at_or_above(n, v), 5),
                        "honest_at_or_below": round(reference.share_at_or_below(n, v), 5)}
                    for n, v in slice_of(r).items()}

        data = {
            "lane": lane.value, "round": round_no, "room": room, "cap": MAX_RULES,
            "rules": [{**r.model_dump(mode="json"), **stats.get(r.id, {"true_hits": 0, "false_hits": 0})} for r in book.rules],
            "reference": quantiles(reference, names, self.pipeline.catalog),
            "misses": [described(r) for r in mine[:MISSES_PER_REFLECTION]],
            "false_alarms": [{"features": slice_of(r), "rules_fired": [i for v in r.decision.votes if v.voter == lane.value
                                                                    for i in v.rule_ids]}
                             for r in false_alarms[:FALSE_ALARMS_PER_REFLECTION]],
        }
        if not mine and not false_alarms:
            return
        instructions = INSTRUCTIONS.format(lane=lane.value, max_rules=MAX_RULES_PER_REFLECTION, room=room, cap=MAX_RULES)
        proposal = self.brain.ask(lane.value, ReflectionProposal, instructions, data)
        outcome = {"proposal": proposal.model_dump(mode="json") if proposal else None, "misses": len(mine),
                   "false_alarms": len(false_alarms), "deployed": [], "vetoed": [], "removed": []}
        if proposal is not None:
            try:
                self._apply(round_no, lane, proposal, replayed, out, outcome, stats)
            except Exception as exc:  # a strange model reply must never stop a round
                log.exception("applying the %s reflection failed", lane.value)
                outcome["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        self.store.log_rows("reflections", [{"run_id": self.run_id, "round": round_no, "lane": lane.value, "payload": outcome}])

    def _apply(self, round_no: int, lane: Lane, proposal: ReflectionProposal, replayed: list[Record],
               out: RoundLearning, outcome: dict, stats: dict) -> None:
        """Removals need evidence (at least as many false alarms as catches this round) and are capped;
        additions go through validation, the cap, replay and the critic."""
        rules = list(self.pipeline.specialists[lane].rulebook.rules)
        asked = [r.id for r in rules if r.id in set(proposal.remove)]
        removed, kept = [], []
        for rule_id in asked:
            s = stats.get(rule_id, {"true_hits": 0, "false_hits": 0})
            if len(removed) < MAX_REMOVALS_PER_REFLECTION and s["false_hits"] > 0 and s["false_hits"] >= s["true_hits"]:
                removed.append(rule_id)
            else:
                kept.append(rule_id)
        if kept:
            outcome["removal_refused"] = kept
        if removed:
            rules = [r for r in rules if r.id not in removed]
            self._deploy(round_no, lane, rules, {"removed": removed}, None)
            out.removed += removed
            outcome["removed"] = removed
        for spec in proposal.add[:MAX_RULES_PER_REFLECTION]:
            rule, problem = self._candidate(spec, lane)
            if problem is None and len(self.pipeline.specialists[lane].rulebook.rules) >= MAX_RULES:
                problem = f"the {lane.value} rulebook is at its cap of {MAX_RULES}; merge or replace a rule first"
            if problem is None:
                replay = replay_rule(rule, lane, self.pipeline, replayed, self.voter_votes)
                directions = {n: self.pipeline.catalog.direction(n) for n in self.pipeline.catalog.lane_features(lane)}
                approved, reason = self.critic.review_rule(rule, lane, replay, directions)
                problem = None if approved else reason
            book_rules = list(self.pipeline.specialists[lane].rulebook.rules)
            if problem is None:
                self._deploy(round_no, lane, [*book_rules, rule], {"added": [rule.id]}, None)
                self.last_true_hit[rule.id] = round_no
                out.deployed.append(rule.id)
                outcome["deployed"].append(rule.id)
            else:
                self._deploy(round_no, lane, book_rules, {"proposed": [spec.model_dump(mode="json")]}, problem)
                out.vetoed.append((spec.id, problem))
                outcome["vetoed"].append({"rule_id": spec.id, "reason": problem})

    def _candidate(self, spec, lane: Lane) -> tuple[Rule | None, str | None]:
        try:
            rule = Rule.model_validate(spec.model_dump())
            validate_rule(rule, lane, self.pipeline.catalog)
        except Exception as exc:  # anything a model can send: RuleError, ValidationError, TypeError...
            return None, f"invalid rule: {str(exc)[:300]}"
        if any(r.id == rule.id for r in self.pipeline.specialists[lane].rulebook.rules):
            return None, f"a rule named {rule.id} already exists"
        return rule, None


def quantiles(reference: LegitReference, names: list[str], catalog) -> dict[str, dict]:
    """Honest reference ranges per feature, and which side of it looks like fraud, for reflection."""
    out = {}
    for name in names:
        column = reference.values.get(name)
        if column:
            n = len(column) - 1
            out[name] = {"min": column[0], "p01": column[int(0.01 * n)], "p05": column[int(0.05 * n)],
                         "p50": column[int(0.5 * n)], "p95": column[int(0.95 * n)], "p99": column[int(0.99 * n)],
                         "max": column[-1], "suspicious_when": catalog.direction(name)}
    return out

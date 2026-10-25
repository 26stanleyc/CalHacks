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
import zlib
import logging
from collections import deque
from dataclasses import dataclass, field

from contracts.schemas import Action, Lane, TrafficSource, Vote
from engine.brains import Brain, parallel
from engine.dsl import MAX_RULES, Rule, condition_features, holds, regular_rules, validate_rule
from engine.learning import hedge, stacking
from engine.learning.stacking import WINDOW_ROUNDS as STACK_WINDOW
from engine.learning.critic import Critic
from engine.learning.drafts import DraftPool
from engine.learning.labels import UNCOVERED, LegitReference, Record
from engine.learning.reflection import INSTRUCTIONS, MAX_RULES_PER_REFLECTION, ReflectionProposal
from engine.learning.replay import VoterVotes, fp_rate, replay_rule
from engine.orchestrator import Orchestrator, hard_veto
from engine.pipeline import DefensePipeline
from engine.specialists import Specialist
from engine.store import LogStore

RETIRE_AFTER = 3  # rounds without a true hit
GUARD_AT = 0.5  # of the FP cap: above this, the rules causing false alarms come out (headroom for drift)
VOTER_AT = 0.5  # of the FP cap: what a retrained voter is calibrated to, out of sample
STACK_AT = 0.75  # of the FP cap: what the learned combiner is calibrated to, on recent honest rounds
STACK_CALIBRATION_ROUNDS = 3  # honest rounds it's calibrated on, pooled (stacking.POOLED_CALIBRATION)
STACK_BOOTSTRAP_AT = 0.1  # of the FP cap: a cautious first combiner after round 1, on out-of-fold scores (S7b);
#                         None turns it off. Strict, because round 2's honest traffic shifts as agents gain history
STACK_CALIBRATION_FOLDS = 3  # the guard, part 1: calibrate with voter copies trained on all the deployed voters'
#                              data except a held-out fold of the round scored (0 = the voters as they were before
#                              this round's retrain, which missed how new attacks make them more aggressive)
STACK_GUARD_TIGHTEN_ABOVE = 0.75  # of the FP cap: a round this high tightens the next target (the guard, part 2)
STACK_GUARD_RELAX_BELOW = 0.25  # of the FP cap: rounds this quiet relax it slowly back toward STACK_AT
STACK_GUARD_MIN = 0.25  # the tightest the feedback goes: a quarter of STACK_AT
STACK_GUARD_TIGHTEN_FACTOR = 0.6  # how hard a round over the line tightens the target
STACK_GUARD_RELAX_FACTOR = 1.1  # how fast quiet rounds relax it
STACK_DROP_VOTERS: tuple[str, ...] = ()  # extra voters the combiner leaves out (e.g. "anomaly")
STACK_BOOTSTRAP_VOTERS: tuple[str, ...] | None = None  # if set, the round-2 bootstrap uses only these voters, no lanes
STACK_WARMUP_ROUNDS = 2  # the first rounds have little agent history yet (no baselines), so honest traffic
#                          looks unlike later rounds; they're left out of calibration once later rounds exist
REVET_HONEST_SHARE = 0.003  # a learned rule firing on more of the growing honest record than this retires
HOLDOUT_HONEST_SHARE = 0.005  # a new rule may fire on at most this share of the newest (held-out) honest round
MIN_HONEST_ROUNDS = 3  # rounds of honest history before a new rule can be judged rare enough to deploy
MIN_HONEST_SAMPLES = 600  # honest payments that must carry each feature a new rule uses (a rarity estimate needs data)
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
    draft_switches: dict[str, int] = field(default_factory=dict)
    drafted: list[str] = field(default_factory=list)  # with drafts on: rules reflection wrote into a new draft


class LearningLoop:
    def __init__(self, pipeline: DefensePipeline, brain: Brain, store: LogStore, run_id: str, fp_cap: float = 0.02,
                 lock: contextlib.AbstractContextManager | None = None, drafts: bool = False, stacking: bool = True):
        self.lock = lock or contextlib.nullcontext()  # held only while the live defense changes
        self.drafts = DraftPool() if drafts else None  # rulebook drafts (stretch): off unless asked for
        self._drafting = False
        self.shelf_of: dict[str, str] = {}  # attacker -> archive shelf, set by the engine each round
        self.watchlist = None  # the threat-intel watchlist (M11), when the engine runs one
        self.watch_true_hits: dict[str, int] = {}
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
        self.stacking = stacking  # learn the orchestrator's combiner (engine/learning/stacking.py)
        self.stack_records: deque[list[Record]] = deque(maxlen=STACK_WINDOW)  # the combiner's training rounds
        self.stack_round_nos: deque[int] = deque(maxlen=STACK_WINDOW)
        self.example_index: dict[int, int] = {}  # round -> its position in example_rounds
        self.stack_margin = 1.0  # the guard's feedback on STACK_AT, from the false alarms the combiner really made
        self.stack_target = None  # the FP target the deployed combiner was calibrated to
        self.stack_bootstrap_live = False  # the deployed combiner is the round-1 bootstrap
        self.stack_version = 0
        self._closest: dict[str, str] = {}  # uncovered miss -> the lane whose features come closest
        self._holdout: list[Record] = []  # the newest honest round, held out from threshold picking

    # --- the round ---------------------------------------------------------------------------

    def learn(self, round_no: int, records: list[Record]) -> RoundLearning:
        self.pipeline = copy.copy(self.live)  # staging: the same features and voters, its own rulebooks and weights
        self.pipeline.specialists = dict(self.live.specialists)
        self.pipeline.orchestrator = Orchestrator(costs=self.live.orchestrator.costs,
                                                  weights=dict(self.live.orchestrator.weights),
                                                  combiner=self.live.orchestrator.combiner)
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
        # Reflection picks thresholds from older honest rounds only; the newest round is held out to
        # validate them, so an overfit threshold shows up before it's deployed.
        older = [r for rnd in list(self.honest)[:-1] for r in rnd]
        self._holdout = list(self.honest[-1]) if len(self.honest) > 1 else []
        reflection_reference = LegitReference([r for rnd in self.recent for r in rnd] + older) if older else reference
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
        self.watch_true_hits = {rid: s["true_hits"] for rid, s in stats.items() if rid.split(".", 1)[-1].startswith("watch_")}
        out.retired = self._retire(round_no)
        out.removed += self._revet(round_no)
        out.removed += self._guard(round_no, records, stats)
        start = {lane: s.rulebook for lane, s in self.pipeline.specialists.items()}
        self._drafting = self.drafts is not None  # reflection's edits go into a draft, not live
        # Every specialist reflects at once (the model calls run in parallel); each lane only changes its
        # own rulebook, and the edits are applied one lane at a time, in lane order.
        lanes = list(self.pipeline.lanes)
        asks = [self._reflection_request(round_no, lane, records, reflection_reference, out, stats) for lane in lanes]
        proposals = parallel(lambda ask: ask and self.brain.ask(ask["lane"].value, ReflectionProposal, ask["instructions"],
                                                                ask["data"]), asks)
        for ask, proposal in zip(asks, proposals):
            if ask:
                self._reflect(round_no, ask, proposal, replayed, out, stats)
        self._drafting = False
        if self.drafts is not None:
            self._choose_drafts(round_no, start, replayed, out)
        if self.watchlist is not None:
            self.store.log_rows("threat_reports", self.watchlist.step(self, round_no, replayed))

        out.critic_agreement = self.critic.end_round()
        self.store.log_rows("critic_checks", [
            {"run_id": self.run_id, "round": round_no, "kind": c.kind, "critic": c.critic, "automated": c.automated,
             "agree": c.agree, "second_opinion": c.second_opinion} for c in self.critic.checks])

        self.hedge_weights = hedge.update(self.hedge_weights, records, self.pipeline.orchestrator.costs)
        self.example_rounds.append([(r.features, r.truth.is_fraud) for r in records])
        self.example_index[round_no] = len(self.example_rounds) - 1
        weights = self._relative(self.hedge_weights)
        self.voter_caps = self._calibrate_voters(weights, records)
        for voter in self.pipeline.voters:
            weights[voter.name] = min(weights.get(voter.name, 1.0), self.voter_caps.get(voter.name, 1.0))
        examples = [e for rnd in self.example_rounds for e in rnd]
        combiner = self._train_combiner(round_no, records)
        with self.lock:  # commit everything at once
            self.live.specialists = dict(self.pipeline.specialists)
            self.live.orchestrator.weights = weights
            if self.stacking:  # None (too little data, or no safe calibration) falls back to the noisy-OR
                self.live.orchestrator.combiner = combiner
            for r in records:  # agent baselines learn from honest payments only
                if not r.truth.is_fraud:
                    self.live.features.learn_baseline(r.tx)
            for voter in self.live.voters:
                voter.retrain(examples)
        out.weights = dict(weights)
        return out

    def _train_combiner(self, round_no: int, records: list[Record]):
        """Retrain the orchestrator's learned combiner (engine/learning/stacking.py). Every input is out of
        sample: the specialists' votes as cast, and out-of-fold scores from the extra voters. Vetoed
        payments and honest mandate breaches are left out: the veto decides those whatever the scores say.
        Calibration uses the newest STACK_CALIBRATION_ROUNDS rounds' honest payments, re-scored by the staged rulebooks."""
        if not self.stacking:
            return None
        self._guard_feedback(records)
        self.stack_records.append([r for r in records if not r.decision.vetoed_by
                                   and not (r.truth.breaches_mandate and not r.truth.is_fraud)])
        self.stack_round_nos.append(round_no)
        window = list(self.stack_records)
        settled = [rnd for rnd, no in zip(window, self.stack_round_nos) if no > STACK_WARMUP_ROUNDS]
        calibrate_on = (settled or window)[-STACK_CALIBRATION_ROUNDS:]
        lanes = {lane.value for lane in self.pipeline.lanes}
        # After one round there are no out-of-sample votes from trained voters yet; the bootstrap (S7b) uses
        # out-of-fold scores for that first combiner, at a much stricter FP target (STACK_BOOTSTRAP_AT).
        bootstrap = STACK_BOOTSTRAP_AT is not None and len(window) == 1
        use_oof = stacking.OUT_OF_FOLD or bootstrap
        if not use_oof:  # the extra voters' votes as cast: out of sample in time
            oof = {r.tx.tx_id: {v.voter: v.score for v in r.decision.votes if v.voter not in lanes}
                   for rnd in window for r in rnd}
        else:
            oof = self._out_of_fold([r for rnd in window for r in rnd])

        only = STACK_BOOTSTRAP_VOTERS if bootstrap else None

        def keep(name: str) -> bool:
            return name not in STACK_DROP_VOTERS and (only is None or name in only)

        def extra(r: Record) -> list[Vote]:
            return [Vote.model_construct(tx_id=r.tx.tx_id, voter=name, score=score, vote=Action.ALLOW, rule_ids=[],
                                         rationale=None) for name, score in oof.get(r.tx.tx_id, {}).items() if keep(name)]

        def lane_votes(r: Record, cast: bool) -> list[Vote]:
            if only is not None:
                return []
            return [v for v in r.decision.votes if v.voter in lanes] if cast else \
                [self.pipeline.vote(lane, r.tx, r.features) for lane in self.pipeline.lanes]

        rounds = [[(lane_votes(r, True) + extra(r), r.tx.amount_cents, r.truth.is_fraud) for r in rnd] for rnd in window]
        if use_oof:
            calibration = [[(lane_votes(r, False) + extra(r), r.tx.amount_cents) for r in rnd if not r.truth.is_fraud]
                           for rnd in calibrate_on]
        else:  # re-scored by the staged defense: its new rulebooks, and extra voters as they'll be deployed
            nos = [no for rnd, no in zip(window, self.stack_round_nos) if any(rnd is c for c in calibrate_on)]
            fresh = self._held_out_scores(calibrate_on, nos, STACK_CALIBRATION_FOLDS) if STACK_CALIBRATION_FOLDS else None

            def voters_now(r: Record) -> list[Vote]:
                if fresh is None:
                    return [v for v in (voter.score(r.tx, r.features) for voter in self.pipeline.voters) if keep(v.voter)]
                return [Vote.model_construct(tx_id=r.tx.tx_id, voter=name, score=score, vote=Action.ALLOW, rule_ids=[],
                                             rationale=None) for name, score in fresh.get(r.tx.tx_id, {}).items() if keep(name)]

            calibration = [[(lane_votes(r, False) + voters_now(r), r.tx.amount_cents) for r in rnd if not r.truth.is_fraud]
                           for rnd in calibrate_on]
        target = self.fp_cap * (STACK_BOOTSTRAP_AT if bootstrap else STACK_AT * self.stack_margin)
        combiner = stacking.train(rounds, calibration, self.pipeline.orchestrator.costs, target, self.stack_version + 1,
                                  min_rounds=1 if bootstrap else stacking.MIN_ROUNDS)
        if combiner is not None:
            self.stack_version = combiner.version
            self.stack_target = target
        self.stack_bootstrap_live = bootstrap and combiner is not None
        return combiner

    def _guard_feedback(self, records: list[Record]) -> None:
        """The guard, part 2: compare the false alarms the deployed combiner actually made this round with
        the cap. A round near or over the cap tightens the next calibration target; quiet rounds relax it
        slowly. This corrects drift the calibration can't see (new attacks reshaping rules and models)."""
        if self.live.orchestrator.combiner is None or self.stack_bootstrap_live:
            return  # the bootstrap has its own, stricter target; round 2's warm-up shift says little about later rounds
        honest = [r for r in records if not r.truth.is_fraud and not r.truth.breaches_mandate]
        if not honest:
            return
        fp = sum(r.decision.action != Action.ALLOW for r in honest) / len(honest)
        if fp > self.fp_cap * STACK_GUARD_TIGHTEN_ABOVE:
            self.stack_margin = max(STACK_GUARD_MIN, self.stack_margin * STACK_GUARD_TIGHTEN_FACTOR)
        elif fp < self.fp_cap * STACK_GUARD_RELAX_BELOW:
            self.stack_margin = min(1.0, self.stack_margin * STACK_GUARD_RELAX_FACTOR)

    def _held_out_scores(self, rounds: list[list[Record]], round_nos: list[int], folds: int) -> dict[str, dict[str, float]]:
        """The guard, part 1: each extra voter's score on each calibration round's honest payments, from a
        copy trained on everything the deployed (just retrained) voter is trained on, except a held-out fold
        of that round. That's how the deployed voter will treat honest traffic it hasn't seen.
        Scoring with the voter from before the retrain missed how the newest attacks make it more
        aggressive (fresh seeds: 3-4.7% false alarms in round 4). Leaving a whole round out was too
        pessimistic (0% false alarms, catch collapsed). Both are logged in evidence/stacking/strategy-log.md."""
        out: dict[str, dict[str, float]] = {}
        for rnd, no in zip(rounds, round_nos):
            idx = self.example_index.get(no)
            fold_of = {r.tx.tx_id: zlib.crc32(r.tx.tx_id.encode()) % folds for r in rnd}
            honest = [r for r in rnd if not r.truth.is_fraud]
            for f in range(folds):
                held = {r.tx.tx_id for r in rnd if fold_of[r.tx.tx_id] == f}
                examples = [(fv, y) for i, ex in enumerate(self.example_rounds) for fv, y in ex
                            if i != idx or fv.tx_id not in held]
                for voter in self.pipeline.voters:
                    shadow = copy.deepcopy(voter)
                    shadow.retrain(examples)
                    for r in honest:
                        if r.tx.tx_id in held:
                            out.setdefault(r.tx.tx_id, {})[voter.name] = shadow.score(r.tx, r.features).score
        return out

    def _out_of_fold(self, rows: list[Record], folds: int = 5) -> dict[str, dict[str, float]]:
        """Each extra voter's score on each row from a copy trained without that row's fold."""
        fold_of = {r.tx.tx_id: zlib.crc32(r.tx.tx_id.encode()) % folds for r in rows}
        out: dict[str, dict[str, float]] = {r.tx.tx_id: {} for r in rows}
        for voter in self.pipeline.voters:
            for f in range(folds):
                shadow = copy.deepcopy(voter)
                shadow.retrain([(r.features, r.truth.is_fraud) for r in rows if fold_of[r.tx.tx_id] != f])
                for r in rows:
                    if fold_of[r.tx.tx_id] == f:
                        out[r.tx.tx_id][voter.name] = shadow.score(r.tx, r.features).score
        return out

    def _relative(self, hedge_weights: dict[str, float]) -> dict[str, float]:
        """Weights on the specialists' scale: the best specialist weighs 1, and extra voters are measured
        against it, so a voter's cap means the same thing however Hedge ranks it."""
        lanes = {lane.value for lane in self.pipeline.lanes}
        top = max((w for v, w in hedge_weights.items() if v in lanes), default=1.0) or 1.0
        return {v: round(min(1.0, w / top) if v in lanes else w / top, 6) for v, w in hedge_weights.items()}

    def _calibrate_voters(self, weights: dict[str, float], latest: list[Record]) -> dict[str, float]:
        """Each extra voter's cap, vetted like a rule but out of sample: a shadow copy trained on every
        round but the latest is replayed on the latest round's honest payments, and the cap is the
        largest weight (up to 1) that keeps the defense under VOTER_AT of the FP cap there. A voter
        with too few labeled rounds, or one that goes over the target even at the floor, sits out
        (weight 0): under the cost matrix even a 5% vote can block an expensive honest payment."""
        caps = {}
        recent = list(self.honest)[-2:] if len(self.honest) > 1 else [latest]
        rounds = [[r for r in rnd if not r.truth.is_fraud and not r.truth.breaches_mandate] for rnd in recent]
        rounds = [rnd for rnd in rounds if rnd]
        honest = [r for rnd in rounds for r in rnd]
        target = self.fp_cap * VOTER_AT
        for voter in self.pipeline.voters:
            if len(self.example_rounds) < MIN_HONEST_ROUNDS or not honest:  # too few labels to trust it yet
                caps[voter.name] = 0.0
                continue
            shadow = copy.deepcopy(voter)  # trained without the rounds it's judged on
            held = 2 if len(self.example_rounds) > 2 else 1
            shadow.retrain([e for rnd in self.example_rounds[:-held] for e in rnd])
            cap = 1.0
            # judged on its worst held-out round, not the average: drift shows up one round at a time
            worst = lambda c: max(self._shadow_fp(shadow, {**weights, voter.name: c}, rnd) for rnd in rounds)
            while cap > hedge.FLOOR and worst(cap) > target:
                cap = max(hedge.FLOOR, cap * 0.7)
            if cap == hedge.FLOOR and worst(cap) > target:
                cap = 0.0
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
        # A rule whose features were missing on most payments couldn't have caught anything: its
        # retirement clock doesn't run that round (e.g. agent baselines before agents have history).
        for specialist in self.pipeline.specialists.values():
            for rule in specialist.rulebook.rules:
                names = condition_features(rule.when)
                present = sum(all(n in r.features.values for n in names) for r in records)
                if records and present < 0.1 * len(records):
                    self.last_true_hit[rule.id] = round_no
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
            keep = [r for r in specialist.rulebook.rules  # watch rules have their own lifecycle (spec 3.8)
                    if r.watch or round_no - self.last_true_hit.get(r.id, round_no) < RETIRE_AFTER]
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

    def _choose_drafts(self, round_no: int, start: dict, replayed: list[Record], out: RoundLearning) -> None:
        """Rulebook drafts (spec 3.9 point 4): what reflection wrote this round becomes a new draft instead
        of being deployed directly; then every draft is scored per shelf with the other lanes held
        fixed, the Pareto front is kept, and at most MAX_SWITCHES lanes switch to their best draft."""
        for lane, before in start.items():
            written = self.pipeline.specialists[lane].rulebook
            if written.rules != before.rules:
                self.drafts.propose(lane, before, written)
                self.pipeline.specialists[lane] = Specialist(lane, before, self.pipeline.catalog)
        honest = [r for r in replayed if not r.truth.is_fraud]
        switches = self.drafts.select(self.pipeline, replayed, honest, self.voter_votes, self.shelf_of,
                                      self.fp_cap * GUARD_AT)
        out.draft_switches = {}
        for lane, (index, book, draft) in switches.items():
            live = {r.id for r in self.pipeline.specialists[lane].rulebook.rules}
            for rule in book.rules:  # a rule going live gets its full grace period before retirement
                if rule.id not in live:
                    self.last_true_hit[rule.id] = round_no
            # watch rules (and promoted ones) belong to the watchlist's lifecycle: a switch keeps the live ones
            watchlisted = [r for r in self.pipeline.specialists[lane].rulebook.rules if ".watch_" in r.id]
            rules = [r for r in book.rules if ".watch_" not in r.id] + watchlisted
            if sum(not r.watch for r in rules) > MAX_RULES:
                continue
            self._deploy(round_no, lane, rules, {"draft_switch": index, "overall": round(draft.overall, 4),
                                                          "fp": round(draft.fp, 4), "shelf_scores": draft.shelf_scores},
                         None, draft=index)
            out.draft_switches[lane.value] = index

    def _deploy(self, round_no: int, lane: Lane, rules: list[Rule], diff: dict, veto: str | None, draft: int = 0) -> None:
        """Change the staging copy (committed at the end of learn) and log the version."""
        book = self.pipeline.specialists[lane].rulebook
        gone = [*diff.get("retired", []), *diff.get("revet_removed", []), *diff.get("fp_guard_removed", []),
                *diff.get("watch_expired", [])]
        if self.drafts is not None and gone:
            self.drafts.forget(lane, gone)
        if veto is None:
            new = book.model_copy(update={"rules": rules, "version": book.version + 1})
            self.pipeline.specialists[lane] = Specialist(lane, new, self.pipeline.catalog)
            book = new
        self.store.log_rows("rulebook_versions", [{
            "run_id": self.run_id, "round": round_no, "lane": lane.value, "draft": draft,
            "rules": [r.model_dump(mode="json") for r in book.rules], "diff": diff, "deployed": veto is None and not self._drafting,
            "veto_reason": veto}])

    # --- reflection -----------------------------------------------------------------------------

    def _reflection_request(self, round_no: int, lane: Lane, records: list[Record], reference: LegitReference,
                            out: RoundLearning, stats: dict) -> dict | None:
        """What a specialist is asked to reflect on, or None when it has nothing to learn from."""
        names = self.pipeline.catalog.lane_features(lane)
        mine = [r for r in records if (r.missed or r.code_missed) and (out.assignments.get(r.tx.tx_id) == lane.value
                                                     or self._closest.get(r.tx.tx_id) == lane.value)]
        false_alarms = [r for r in records if r.false_positive
                        and any(v.voter == lane.value and v.rule_ids for v in r.decision.votes)]
        book = self.pipeline.specialists[lane].rulebook
        room = MAX_RULES - regular_rules(book)

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
            return None
        instructions = INSTRUCTIONS.format(lane=lane.value, max_rules=MAX_RULES_PER_REFLECTION, room=room, cap=MAX_RULES)
        return {"lane": lane, "instructions": instructions, "data": data, "misses": len(mine),
                "false_alarms": len(false_alarms)}

    def _reflect(self, round_no: int, ask: dict, proposal: ReflectionProposal | None, replayed: list[Record],
                 out: RoundLearning, stats: dict) -> None:
        lane = ask["lane"]
        outcome = {"proposal": proposal.model_dump(mode="json") if proposal else None, "misses": ask["misses"],
                   "false_alarms": ask["false_alarms"], "deployed": [], "vetoed": [], "removed": []}
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
            if problem is None and not any(self.pipeline.catalog.about_payer(n) for n in condition_features(rule.when)):
                problem = ("it only describes the merchant's recent traffic, so it would flag every payer there, honest "
                           "ones included; pair it with a signal about the payer")
            if problem is None and regular_rules(self.pipeline.specialists[lane].rulebook) >= MAX_RULES:
                problem = f"the {lane.value} rulebook is at its cap of {MAX_RULES}; merge or replace a rule first"
            if problem is None and len(self.honest) < MIN_HONEST_ROUNDS:
                problem = (f"too little honest history yet ({len(self.honest)} of {MIN_HONEST_ROUNDS} rounds) to judge "
                           f"how often it would fire on honest payments")
            if problem is None:
                honest_all = [r for rnd in self.honest for r in rnd if not r.truth.breaches_mandate]
                for name in sorted(set(condition_features(rule.when))):
                    carried = sum(name in r.features.values for r in honest_all)
                    if carried < min(MIN_HONEST_SAMPLES, len(honest_all) // 2):
                        problem = (f"too few honest payments carry {name} yet ({carried} of {MIN_HONEST_SAMPLES}) "
                                   f"to judge how rare its threshold is")
                        break
            if problem is None and self._holdout:
                names = set(self.pipeline.catalog.lane_features(lane))
                honest_new = [r for r in self._holdout if not r.truth.breaches_mandate]
                fires = sum(holds(rule.when, {k: v for k, v in r.features.values.items() if k in names}) for r in honest_new)
                if honest_new and fires / len(honest_new) > HOLDOUT_HONEST_SHARE:
                    problem = (f"overfits: fires on {fires / len(honest_new):.1%} of the newest honest round, which it "
                               f"wasn't learned from (limit {HOLDOUT_HONEST_SHARE:.1%})")
            if problem is None:
                replay = replay_rule(rule, lane, self.pipeline, replayed, self.voter_votes)
                directions = {n: self.pipeline.catalog.direction(n) for n in self.pipeline.catalog.lane_features(lane)}
                approved, reason = self.critic.review_rule(rule, lane, replay, directions)
                problem = None if approved else reason
            book_rules = list(self.pipeline.specialists[lane].rulebook.rules)
            if problem is None:
                self._deploy(round_no, lane, [*book_rules, rule], {"added": [rule.id]}, None)
                if self._drafting:
                    out.drafted.append(rule.id)
                else:
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
                         "max": column[-1], "suspicious_when": catalog.direction(name),
                         "about": "payer" if catalog.about_payer(name) else "merchant"}
    return out

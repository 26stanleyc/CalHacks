"""The critic (spec 3.3, 4.5, 4.8): assigns misses to lanes and vetoes rule changes, from a fixed
rubric with structured output, so every decision can be checked and logged.

Spot-check (so it isn't an unchecked single point of failure): every decision is compared with an
automated answer computed without any LLM. That is a superset of the spec's 10% sample, since
the automated answers cost nothing:
- miss assignments: the lane whose features best separate that fraud from legitimate traffic
  (labels.best_separating_lane)
- vetoes: the rule replayed on recent traffic; it should pass when it catches fraud and keeps the
  defense's false-positive rate under the cap

When a decision type agrees less than 80% of the time for 2 rounds in a row, it also goes to the
second-opinion model (a different family). When the two disagree, the automated answer decides.
"""

import statistics
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from contracts.schemas import Lane
from engine.brains import Brain, mock_answer
from engine.dsl import Rule
from engine.learning.labels import UNCOVERED, LegitReference, Record, best_separating_lane
from engine.learning.replay import RuleReplay

AGREEMENT_FLOOR = 0.80
ROUNDS_BELOW_FLOOR = 2
MISSES_PER_CALL = 25
MAX_MISSES_FOR_CRITIC = 100  # beyond this, the rest take the automated answer (cost cap)
DEGENERATE_FIRE_SHARE = 0.30  # a rule firing on this share of all traffic is "block everything"
VET_HEADROOM = 0.6  # a new rule must keep the replayed FP rate under this share of the cap


class MissAssignment(BaseModel):
    tx_id: str
    lane: str  # a lane name, or "uncovered" when no lane's features can express it
    reason: str = ""


class MissAssignments(BaseModel):
    assignments: list[MissAssignment] = Field(default_factory=list)


class RuleVerdict(BaseModel):
    approve: bool
    reason: str


ASSIGN_RUBRIC = """You are the critic for a team of fraud specialists. Each specialist only sees its own lane's
features. For each missed fraud payment, name the one lane whose features best show it is fraud
(the feature values furthest outside the honest reference ranges), or "uncovered" if no lane's
features look unusual. Use only these lane names: {lanes}, or "uncovered". Give a one-line reason."""

VETO_RUBRIC = """You are the critic for a fraud defense. A specialist proposes a new rule. Approve it only if:
- it catches real fraud on the replayed traffic (fires_on_fraud > 0, ideally newly_caught_misses > 0)
- the whole defense's false-positive rate with the rule stays at or under the cap ({cap:.1%})
- it isn't degenerate (e.g. blocking every payment over some amount, or firing on a large share of all traffic)
- each comparison points the way its feature looks suspicious (feature_directions: "low" means low values
  look like fraud, so a rule must not flag high values of it; such rules overfit to the moment)
Reply approve true or false with a one-line reason."""


def feature_stats(reference: LegitReference, names: list[str]) -> dict[str, dict]:
    out = {}
    for name in names:
        column = reference.values.get(name)
        if column:
            out[name] = {"median": column[len(column) // 2], "p05": column[int(0.05 * (len(column) - 1))],
                         "p95": column[int(0.95 * (len(column) - 1))], "mean": statistics.fmean(column),
                         "std": statistics.pstdev(column)}
    return out


@mock_answer(MissAssignments)
def _mock_assign(data: dict) -> MissAssignments:
    """The lane with the largest z-score against the honest reference (the automated check uses
    percentiles instead, so the two can disagree)."""
    out = []
    for miss in data["misses"]:
        best, best_z = UNCOVERED, 2.5
        for lane, values in miss["lanes"].items():
            for name, value in values.items():
                stats = data["reference"][lane].get(name)
                if stats and stats["std"] > 0:
                    z = abs(value - stats["mean"]) / stats["std"]
                    if z > best_z:
                        best, best_z = lane, z
        out.append(MissAssignment(tx_id=miss["tx_id"], lane=best, reason=f"mock critic: largest z-score {best_z:.1f}"))
    return MissAssignments(assignments=out)


def wrong_way(cond: dict, directions: dict[str, str]) -> str | None:
    """A comparison that flags the safe side of its feature, if any."""
    if "feature" in cond:
        side = directions.get(cond["feature"], "either")
        if (side == "low" and cond["op"] in (">", ">=")) or (side == "high" and cond["op"] in ("<", "<=")):
            return cond["feature"]
        return None
    (key, inner), = cond.items()
    if key == "not":
        return None  # a negation flips the meaning; leave it to the replay
    return next((f for f in (wrong_way(c, directions) for c in inner) if f), None)


@mock_answer(RuleVerdict)
def _mock_verdict(data: dict) -> RuleVerdict:
    replay = data["replay"]
    backwards = wrong_way(data["rule"]["when"], data.get("feature_directions", {}))
    if backwards:
        return RuleVerdict(approve=False, reason=f"mock critic: flags the safe side of {backwards}")
    if replay["fires_on_fraud"] == 0:
        return RuleVerdict(approve=False, reason="mock critic: catches no fraud")
    if replay["fires_on_share_of_all"] > DEGENERATE_FIRE_SHARE:
        return RuleVerdict(approve=False, reason="mock critic: fires on too much of all traffic")
    if replay["fires_on_legit"] > 0.005 * max(1, replay["legit_payments"]):
        return RuleVerdict(approve=False, reason="mock critic: fires on more than 0.5% of honest payments")
    return RuleVerdict(approve=True, reason="mock critic: catches fraud and stays quiet on honest payments")


def automated_verdict(replay: RuleReplay, cap: float, rule: Rule, directions: dict[str, str]) -> dict:
    backwards = wrong_way(rule.when, directions)
    approve = (replay.fires_fraud > 0 and replay.system_fp_rate <= cap and replay.fires_share <= DEGENERATE_FIRE_SHARE
               and backwards is None)
    reason = (f"replay: catches {replay.fires_fraud} fraud, system FP {replay.system_fp_rate:.2%} vs cap {cap:.0%}, "
              f"fires on {replay.fires_share:.1%}" + (f"; flags the safe side of {backwards}" if backwards else ""))
    return {"approve": approve, "reason": reason}


@dataclass
class CriticCheck:
    """One critic decision next to its automated answer (one critic_checks row)."""

    kind: str  # miss_assignment | veto
    critic: dict
    automated: dict
    agree: bool
    second_opinion: dict | None = None


@dataclass
class Critic:
    brain: Brain
    lanes: tuple[Lane, ...]
    fp_cap: float = 0.02
    history: dict[str, list[float]] = field(default_factory=lambda: {"miss_assignment": [], "veto": []})
    checks: list[CriticCheck] = field(default_factory=list)  # this round's
    unanswered: int = 0

    def start_round(self) -> None:
        self.checks = []
        self.unanswered = 0  # decisions the critic didn't answer usably this round

    def routed_to_second_opinion(self, kind: str) -> bool:
        recent = self.history[kind][-ROUNDS_BELOW_FLOOR:]
        return len(recent) == ROUNDS_BELOW_FLOOR and all(a < AGREEMENT_FLOOR for a in recent)

    def end_round(self) -> float | None:
        """Close the round's agreement per decision type; returns the overall agreement."""
        for kind in self.history:
            mine = [c.agree for c in self.checks if c.kind == kind]
            if mine:
                self.history[kind].append(sum(mine) / len(mine))
        return sum(c.agree for c in self.checks) / len(self.checks) if self.checks else None

    # --- miss assignment -------------------------------------------------------------------

    def assign(self, misses: list[Record], lane_features: dict[Lane, list[str]], reference: LegitReference) -> dict[str, str]:
        """tx_id -> lane name or 'uncovered' for every miss."""
        automated = {m.tx.tx_id: best_separating_lane(m.features.values, lane_features, reference) for m in misses}
        reference_stats = {lane.value: feature_stats(reference, names) for lane, names in lane_features.items()}
        names = ", ".join(lane.value for lane in self.lanes)
        result: dict[str, str] = {}
        critic_misses = misses[:MAX_MISSES_FOR_CRITIC]
        second = self.routed_to_second_opinion("miss_assignment")
        for start in range(0, len(critic_misses), MISSES_PER_CALL):
            chunk = critic_misses[start:start + MISSES_PER_CALL]
            data = {"reference": reference_stats, "misses": [
                {"tx_id": m.tx.tx_id, "lanes": {lane.value: {n: m.features.values[n] for n in feats if n in m.features.values}
                                                 for lane, feats in lane_features.items()}} for m in chunk]}
            answer = self.brain.ask("critic", MissAssignments, ASSIGN_RUBRIC.format(lanes=names), data)
            given = self._valid_assignments(answer)
            other = self._valid_assignments(self.brain.ask("critic_second", MissAssignments, ASSIGN_RUBRIC.format(lanes=names), data)) if second else {}
            for m in chunk:
                tx_id = m.tx.tx_id
                if tx_id not in given:  # no usable critic answer: the automated one stands, and nothing is spot-checked
                    result[tx_id] = automated[tx_id]
                    self.unanswered += 1
                    continue
                critic_lane = given[tx_id]
                final, second_row = critic_lane, None
                if second:
                    second_lane = other.get(tx_id, automated[tx_id])
                    second_row = {"lane": second_lane}
                    if second_lane != critic_lane:
                        final = automated[tx_id]
                result[tx_id] = final
                self.checks.append(CriticCheck("miss_assignment", {"tx_id": tx_id, "lane": critic_lane},
                                               {"lane": automated[tx_id]}, critic_lane == automated[tx_id], second_row))
        for m in misses[MAX_MISSES_FOR_CRITIC:]:
            result[m.tx.tx_id] = automated[m.tx.tx_id]
        return result

    def _valid_assignments(self, answer: MissAssignments | None) -> dict[str, str]:
        allowed = {lane.value for lane in self.lanes} | {UNCOVERED}
        return {a.tx_id: a.lane for a in (answer.assignments if answer else []) if a.lane in allowed}

    # --- vetoes -------------------------------------------------------------------------------

    def review_rule(self, rule: Rule, lane: Lane, replay: RuleReplay, directions: dict[str, str]) -> tuple[bool, str]:
        """(deploy?, reason). Two checks are also enforced in code, whatever the critic says: a rule
        that flags the safe side of a feature, or that pushes the replayed false-positive rate over
        VET_HEADROOM of the cap, is never deployed. When the critic gives no usable answer, the
        automated one decides and nothing is spot-checked."""
        data = {"lane": lane.value, "rule": rule.model_dump(mode="json"), "replay": replay.summary(), "fp_cap": self.fp_cap,
                "feature_directions": directions}
        rubric = VETO_RUBRIC.format(cap=self.fp_cap)
        verdict = self.brain.ask("critic", RuleVerdict, rubric, data)
        auto = automated_verdict(replay, self.fp_cap, rule, directions)
        if verdict is None:
            self.unanswered += 1
            approve, reason = auto["approve"], f"critic unavailable; {auto['reason']}"
        else:
            approve, reason, second_row = verdict.approve, verdict.reason, None
            if self.routed_to_second_opinion("veto"):
                other = self.brain.ask("critic_second", RuleVerdict, rubric, data)
                second_row = other.model_dump() if other else None
                if other is not None and other.approve != approve:
                    approve, reason = auto["approve"], f"critic and second opinion disagree; {auto['reason']}"
            self.checks.append(CriticCheck("veto", {"rule_id": rule.id, **verdict.model_dump()}, auto,
                                           verdict.approve == auto["approve"], second_row))
        backwards = wrong_way(rule.when, directions)
        if approve and backwards:
            return False, f"flags the safe side of {backwards} (it looks suspicious when {directions[backwards]})"
        limit = self.fp_cap * VET_HEADROOM
        if approve and replay.system_fp_rate > limit:
            return False, (f"replay pushes the false-positive rate to {replay.system_fp_rate:.2%}, over "
                           f"{limit:.1%} ({VET_HEADROOM:.0%} of the {self.fp_cap:.0%} cap, leaving room for drift)")
        return approve, reason

"""Rulebook drafts (spec 3.9 point 4, stretch; GEPA-style Pareto selection).

1. Each specialist keeps up to MAX_DRAFTS rulebook drafts. Reflection writes a new draft by editing
   an existing one (the deployed one).
2. Every draft is scored per shelf by replaying recent traffic, with the other specialists' deployed
   rulebooks held fixed: on each shelf, the share of its fraud the defense would catch with this
   draft in place.
3. Drafts that aren't best on at least one shelf are dropped (the Pareto front).
4. The best overall draft whose replayed false-positive rate stays under the limit is deployed
   (the critic already reviewed every rule in it when reflection wrote it).
5. At most MAX_SWITCHES specialists switch drafts per round, the biggest improvements first.

The deployed rulebook is always one of the drafts, so a switch must beat what is live now. Rules
that rulebook hygiene removes (retired, failed re-vetting, removed by the false-positive guard)
are removed from every draft too, so a switch never brings them back.
"""

from dataclasses import dataclass, field

from contracts.schemas import Action, Lane
from engine.dsl import Rulebook
from engine.learning.labels import Record
from engine.learning.replay import VoterVotes, decide_now, fp_rate
from engine.pipeline import DefensePipeline
from engine.specialists import Specialist

MAX_DRAFTS = 3
MAX_SWITCHES = 2


@dataclass
class Draft:
    rulebook: Rulebook
    draft_id: int = 0  # stable within its lane; logged with a switch
    shelf_scores: dict[str, float] = field(default_factory=dict)
    overall: float = 0.0
    fp: float = 0.0


def shelf_catch(pipeline: DefensePipeline, lane: Lane, rulebook: Rulebook, records: list[Record], voters: VoterVotes,
                shelf_of: dict[str, str]) -> dict[str, float]:
    """Per shelf: the share of its fraud caught with this lane's rulebook swapped in."""
    specialist = Specialist(lane, rulebook, pipeline.catalog, validate=False)
    caught: dict[str, list[bool]] = {}
    for r in records:
        sid = shelf_of.get(r.tx.attacker_id or "")
        if not r.truth.is_fraud or sid is None:
            continue
        caught.setdefault(sid, []).append(decide_now(pipeline, r, voters, lane, specialist).action != Action.ALLOW)
    return {sid: sum(hits) / len(hits) for sid, hits in caught.items()}


def pareto(drafts: list[Draft]) -> list[Draft]:
    """Keep each draft that is best (ties included) on at least one shelf."""
    shelves = {sid for d in drafts for sid in d.shelf_scores}
    if not shelves:
        return drafts[-MAX_DRAFTS:]
    keep = []
    for d in drafts:
        if any(d.shelf_scores.get(sid, 0.0) >= max(o.shelf_scores.get(sid, 0.0) for o in drafts) for sid in shelves):
            keep.append(d)
    return keep[-MAX_DRAFTS:]


class DraftPool:
    """Each lane's drafts, re-scored every round."""

    def __init__(self):
        self.drafts: dict[Lane, list[Draft]] = {}
        self.next_id: dict[Lane, int] = {}

    def _add(self, lane: Lane, rulebook: Rulebook) -> Draft:
        pool = self.drafts.setdefault(lane, [])
        same = next((d for d in pool if d.rulebook.rules == rulebook.rules), None)
        if same is not None:
            return same
        draft = Draft(rulebook, draft_id=self.next_id.get(lane, 0))
        self.next_id[lane] = draft.draft_id + 1
        pool.append(draft)
        return draft

    def propose(self, lane: Lane, deployed: Rulebook, candidate: Rulebook) -> None:
        self._add(lane, deployed)
        self._add(lane, candidate)

    def forget(self, lane: Lane, rule_ids: list[str]) -> None:
        """Hygiene removed these rules from the live rulebook: remove them from every draft too."""
        gone = set(rule_ids)
        pool = []
        for d in self.drafts.get(lane, []):
            rules = [r for r in d.rulebook.rules if r.id not in gone]
            if rules != d.rulebook.rules:
                d = Draft(d.rulebook.model_copy(update={"rules": rules}), draft_id=d.draft_id)
            if all(o.rulebook.rules != d.rulebook.rules for o in pool):
                pool.append(d)
        if lane in self.drafts:
            self.drafts[lane] = pool

    def select(self, pipeline: DefensePipeline, records: list[Record], honest: list[Record], voters: VoterVotes,
               shelf_of: dict[str, str], fp_limit: float) -> dict[Lane, tuple[int, Rulebook, Draft]]:
        """Score every draft, prune to the Pareto front, and return the switches to make:
        lane -> (draft id, rulebook, draft), at most MAX_SWITCHES, biggest improvement first."""
        options = []
        for lane in list(self.drafts):
            live = self._add(lane, pipeline.specialists[lane].rulebook)
            pool = self.drafts[lane]
            for d in pool:
                d.shelf_scores = shelf_catch(pipeline, lane, d.rulebook, records, voters, shelf_of)
                d.overall = sum(d.shelf_scores.values()) / max(1, len(d.shelf_scores))
                d.fp = fp_rate(pipeline, honest, voters, lane, d.rulebook)
            self.drafts[lane] = pool = pareto(pool)
            eligible = [d for d in pool if d.fp <= fp_limit]
            best = max(eligible, key=lambda d: (d.overall, -d.fp), default=None)
            if best is not None and best is not live and best.overall > live.overall:
                options.append((best.overall - live.overall, lane, best.draft_id, best))
        options.sort(key=lambda o: (-o[0], o[1].value))
        return {lane: (index, d.rulebook, d) for _, lane, index, d in options[:MAX_SWITCHES]}

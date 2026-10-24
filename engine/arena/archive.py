"""The attack archive (spec 3.9 point 1-2; M7): a quality-diversity grid in the style of Rainbow
Teaming, with one shelf per attack type.

- 24 shelves: style (burst / slow drip / many accounts / hijack-or-injection) x amount (under $10 /
  $10-$100 / over $100) x side (merchant / buyer).
- Top 3 per shelf, ranked by attacker profit against the current defense. One of the 3 spots is
  reserved for an outsider; outsiders compete for it only with other outsiders, and the other 2
  spots are open to anyone.
- Duplicate check: an attacker whose playbook is nearly identical to one already on the shelf is
  rejected, even if it scores a little higher.
- Learnability per shelf: catch_rate x (1 - catch_rate) over the last 3 rounds, with the smoothed
  catch rate (caught + 1) / (attempts + 2). A shelf with under 20 recent attempts is "unknown".
- Shelf tags: which specialists usually catch the shelf's attacks and which usually miss them.

Every insertion, rejection and eviction is an event, and every round ends with a snapshot.
"""

import math
import random
from collections import Counter
from dataclasses import dataclass, field

from contracts.schemas import (AmountBand, AttackerRecord, AttackerSource, AttackStyle, Lane, Playbook, ShelfKey,
                               ShelfState, Side)
from engine.arena.playbooks import REALISM

SHELF_SIZE = 3
RECENT_ROUNDS = 3
UNKNOWN_BELOW = 20
DUPLICATE_SIMILARITY = 0.95
LEARNABILITY_SHARE = 0.8  # archive draws: 80% by learnability, 20% uniform
OUTSIDER_SOURCES = (AttackerSource.OUTSIDER, AttackerSource.THREAT_INTEL)

ALL_SHELVES = tuple(ShelfKey(style=s, amount=a, side=d) for s in AttackStyle for a in AmountBand for d in Side)

# Which lanes' signals matter for which styles: a specialist's "area" (spec 3.9 point 3).
LANE_STYLES: dict[Lane, tuple[AttackStyle, ...]] = {
    Lane.TIMING: (AttackStyle.BURST, AttackStyle.SLOW_DRIP),
    Lane.GRAPH: (AttackStyle.MANY_ACCOUNTS,),
    Lane.IDENTITY: (AttackStyle.MANY_ACCOUNTS, AttackStyle.HIJACK_INJECTION),
    Lane.VELOCITY: (AttackStyle.BURST, AttackStyle.SLOW_DRIP),
    Lane.MANDATE: (AttackStyle.HIJACK_INJECTION,),
}


def is_outsider(record: AttackerRecord) -> bool:
    return record.source in OUTSIDER_SOURCES


def similarity(a: Playbook, b: Playbook) -> float:
    """1 - mean normalized distance between the two playbooks' numbers (log scale for scale-like ones)."""
    distances = []
    for name, (lo, hi) in REALISM.items():
        x, y = a.params.get(name, lo), b.params.get(name, lo)
        if name in ("amount_cents", "interval_s", "n_accounts", "n_merchants"):
            x, y, lo, hi = math.log(max(x, 1e-9)), math.log(max(y, 1e-9)), math.log(max(lo, 1e-9)), math.log(hi)
        distances.append(abs(x - y) / ((hi - lo) or 1.0))
    same_text = (a.free_text_payloads == b.free_text_payloads) or not (a.params.get("memo_rate") or b.params.get("memo_rate"))
    return (1.0 - sum(distances) / len(distances)) * (1.0 if same_text else 0.9)


@dataclass
class Shelf:
    key: ShelfKey
    entries: dict[str, AttackerRecord] = field(default_factory=dict)  # attacker id -> record (current score)
    recent: list[tuple[int, int, int]] = field(default_factory=list)  # (round, attempts, caught)
    caught_by: Counter = field(default_factory=Counter)
    missed_by: Counter = field(default_factory=Counter)

    def window(self, round_no: int) -> tuple[int, int]:
        rows = [(a, c) for r, a, c in self.recent if r > round_no - RECENT_ROUNDS]
        return sum(a for a, _ in rows), sum(c for _, c in rows)

    def state(self, round_no: int) -> ShelfState:
        attempts, caught = self.window(round_no)
        outsiders = [aid for aid, rec in self.entries.items() if is_outsider(rec)]
        slot = max(outsiders, key=lambda aid: self.entries[aid].profit_cents, default=None)
        return ShelfState(key=self.key, attacker_ids=self.ranked(), outsider_slot=slot, attempts=attempts, caught=caught,
                          unknown=attempts < UNKNOWN_BELOW)

    def ranked(self) -> list[str]:
        return sorted(self.entries, key=lambda aid: (-self.entries[aid].profit_cents, aid))


@dataclass
class Event:
    shelf_id: str
    attacker_id: str
    event: str  # inserted | rejected_profit | rejected_duplicate | evicted | rescored
    detail: dict


class Archive:
    def __init__(self):
        self.shelves: dict[str, Shelf] = {key.id(): Shelf(key) for key in ALL_SHELVES}
        self.events: list[Event] = []  # this round's

    # --- membership ---------------------------------------------------------------------------

    def offer(self, record: AttackerRecord) -> bool:
        """Try to place an attacker on its shelf. Held-out attackers never enter (spec 4)."""
        if record.source == AttackerSource.HELD_OUT:
            raise ValueError("held-out attackers never enter the archive")
        shelf = self.shelves[record.playbook.shelf.id()]
        sid = shelf.key.id()
        for other in self.shelves.values():  # a lineage whose playbook moved shelves leaves its old one
            if other is not shelf and record.attacker_id in other.entries:
                del other.entries[record.attacker_id]
                self.events.append(Event(other.key.id(), record.attacker_id, "evicted", {"moved_to": sid}))
        if record.attacker_id in shelf.entries:  # a lineage already on the shelf: keep its latest playbook
            shelf.entries[record.attacker_id] = record
            return True
        for aid, other in shelf.entries.items():
            sim = similarity(record.playbook, other.playbook)
            if sim >= DUPLICATE_SIMILARITY:
                self.events.append(Event(sid, record.attacker_id, "rejected_duplicate", {"similar_to": aid, "similarity": round(sim, 3)}))
                return False
        candidate = {**shelf.entries, record.attacker_id: record}
        keep = self._select(candidate)
        if record.attacker_id not in keep:
            rivals = [r for r in shelf.entries.values() if is_outsider(r) == is_outsider(record)] or list(shelf.entries.values())
            weakest = min(rivals, key=lambda r: r.profit_cents)
            self.events.append(Event(sid, record.attacker_id, "rejected_profit",
                                     {"profit_cents": record.profit_cents, "needed": weakest.profit_cents}))
            return False
        for aid in set(shelf.entries) - keep:
            self.events.append(Event(sid, aid, "evicted", {"by": record.attacker_id}))
        shelf.entries = {aid: rec for aid, rec in candidate.items() if aid in keep}  # insertion order: reproducible
        self.events.append(Event(sid, record.attacker_id, "inserted", {"profit_cents": record.profit_cents}))
        return True

    @staticmethod
    def _select(entries: dict[str, AttackerRecord]) -> set[str]:
        """The reserved spot goes to the best outsider; the 2 open spots to the best of the rest."""
        by_profit = sorted(entries, key=lambda aid: (-entries[aid].profit_cents, aid))
        best_outsider = next((aid for aid in by_profit if is_outsider(entries[aid])), None)
        rest = [aid for aid in by_profit if aid != best_outsider]
        keep = set(rest[:SHELF_SIZE - 1])
        if best_outsider is not None:
            keep.add(best_outsider)
        else:
            keep = set(by_profit[:SHELF_SIZE - 1])  # the reserved spot stays empty without an outsider
        return keep

    def rescore(self, attacker_id: str, profit_cents: int, round_no: int) -> None:
        for shelf in self.shelves.values():
            if attacker_id in shelf.entries:
                shelf.entries[attacker_id] = shelf.entries[attacker_id].model_copy(
                    update={"profit_cents": profit_cents, "scored_round": round_no})
                self.events.append(Event(shelf.key.id(), attacker_id, "rescored", {"profit_cents": profit_cents}))

    def members(self) -> list[AttackerRecord]:
        return [rec for shelf in self.shelves.values() for rec in shelf.entries.values()]

    # --- outcomes and learnability ------------------------------------------------------------------

    def record_outcomes(self, round_no: int, shelf_id: str, attempts: int, caught: int,
                        caught_by: list[str] = (), missed_by: list[str] = ()) -> None:
        shelf = self.shelves[shelf_id]
        shelf.recent.append((round_no, attempts, caught))
        shelf.recent = [row for row in shelf.recent if row[0] > round_no - RECENT_ROUNDS]
        shelf.caught_by.update(caught_by)
        shelf.missed_by.update(missed_by)

    def learnability(self, shelf_id: str, round_no: int) -> float:
        return self.shelves[shelf_id].state(round_no).learnability

    def area(self, lane: Lane) -> list[str]:
        return [sid for sid, shelf in self.shelves.items() if shelf.key.style in LANE_STYLES.get(lane, ())]

    def sample(self, n: int, round_no: int, rng: random.Random, include=lambda record: True) -> list[AttackerRecord]:
        """Up to n distinct archive attackers to run next round: each draw 80% weighted by its shelf's
        learnability over the last 3 finished rounds, 20% uniform. `round_no` is the round about to run."""
        pool = sorted((rec for rec in self.members() if include(rec)), key=lambda r: r.attacker_id)
        out = []
        while pool and len(out) < n:
            weights = [self.learnability(rec.playbook.shelf.id(), round_no - 1) for rec in pool]
            if rng.random() < LEARNABILITY_SHARE and sum(weights) > 0:
                pick = rng.choices(pool, weights=weights)[0]
            else:
                pick = rng.choice(pool)
            out.append(pick)
            pool.remove(pick)
        return out

    # --- metrics and logs ----------------------------------------------------------------------

    def filled(self) -> list[Shelf]:
        return [s for s in self.shelves.values() if s.entries]

    def coverage(self) -> float:
        return len(self.filled()) / len(self.shelves)

    def defended_share(self, round_no: int, at: float = 0.8) -> float:
        filled = self.filled()
        if not filled:
            return 0.0
        defended = 0
        for shelf in filled:
            attempts, caught = shelf.window(round_no)
            defended += attempts > 0 and caught / attempts >= at
        return defended / len(filled)

    def total_profit(self) -> int:
        return sum(max(0, rec.profit_cents) for rec in self.members())

    def snapshot_rows(self, run_id: str, round_no: int) -> list[dict]:
        rows = []
        for sid, shelf in self.shelves.items():
            state = shelf.state(round_no)
            this_round = [(a, c) for r, a, c in shelf.recent if r == round_no]
            payload = {**state.model_dump(mode="json"),
                       # attempts/caught are this round's (the dashboard sums the last 3); unknown, catch_rate
                       # and learnability are over the 3-round window, given as window_attempts/window_caught
                       "window_attempts": state.attempts, "window_caught": state.caught,
                       "attempts": sum(a for a, _ in this_round), "caught": sum(c for _, c in this_round),
                       "catch_rate": round(state.catch_rate, 4), "learnability": round(state.learnability, 4),
                       "caught_by": [lane for lane, _ in shelf.caught_by.most_common(3)],
                       "missed_by": [lane for lane, _ in shelf.missed_by.most_common(3)],
                       "profits": {aid: shelf.entries[aid].profit_cents for aid in state.attacker_ids}}
            rows.append({"run_id": run_id, "round": round_no, "shelf_id": sid, "payload": payload})
        return rows

    def event_rows(self, run_id: str, round_no: int) -> list[dict]:
        rows = [{"run_id": run_id, "round": round_no, "shelf_id": e.shelf_id, "attacker_id": e.attacker_id,
                 "event": e.event, "detail": e.detail} for e in self.events]
        self.events = []
        return rows

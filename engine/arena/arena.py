"""The Tag-Team Arena's round logic (spec 3.9, 4 steps 3-4 and 11-13; M7), kept out of the engine
so FraudEngine only calls three hooks:

- `attackers_for_round()`: this round's extra attackers: the spawned insiders built last round and
  archive attackers sampled by learnability (80/20). The outsider lineages always run too.
- `after_waves(records)`: shelf outcomes and tags, then every attacker that ran is offered to the
  archive.
- `after_learning(pipeline)`: re-score every archive attacker against the updated defense, choose
  defender actions within the shared budget, build next round's spawned attackers (and the gap
  attacker), and run the exploitability exam on a frozen copy, held-out attackers included.

Held-out attackers (spec 4.13) run only in the exam: never in round traffic, the archive,
reflection, labels or XGBoost training. Exam traffic runs in sandboxes, so it never touches labels
or metrics either.
"""

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from contracts.config import MODEL_ROLES
from contracts.schemas import (Action, AmountBand, AttackerRecord, AttackerSource, AttackFeedback, AttackStyle,
                               GroundTruth, IdentityStatus, Lane, Playbook, ShelfKey, Side, Transaction)
from engine.arena import playbooks
from engine.arena.archive import LANE_STYLES, Archive
from engine.arena.outsiders import shelf_for
from engine.arena.playbooks import REALISM, WAVES
from engine.arena.sandbox import SANDBOX_PAYMENTS, TUNING_TRIES, Sandbox, tune
from engine.arena.spawning import (ACTION_INSTRUCTIONS, BUDGET, GAP_ATTACKER_MIN_SHARE, GAP_PARENT, INSIDER_STEP_UP_PASS,
                                   SPAWN_AT_LEAST_EVERY, ActionChoice, InsiderAttacker, allocate, build_spawned,
                                   shelf_bounds)
from engine.brains import Brain, mock_answer
from engine.sim.world import INJECTION_MEMOS

ARCHIVE_DRAWS = 4
ARCHIVE_PAYMENTS = 10  # per archive attacker per round
SPAWNED_PAYMENTS = 15  # per spawned attacker per round
EXAM_FRESH = 2  # fresh attackers built and tuned for each exam
EXAM_TOP = 3  # archive attackers with the highest current profit


class ArchiveRunner:
    """An archive attacker replaying its committed playbook in round traffic (no adaptation)."""

    def __init__(self, record: AttackerRecord, products: list, identities: dict[str, IdentityStatus], round_seconds: float,
                 mandate_for: playbooks.MandateFor, tag: str):
        self._record = record
        self.attacker_id = record.attacker_id
        self.playbook = record.playbook
        self.products, self.identities, self.round_seconds, self.mandate_for = products, identities, round_seconds, mandate_for
        self.tag = tag
        self.step_up_pass = INSIDER_STEP_UP_PASS
        self.round_stats = {"extraction": 0, "attempts": 0, "caught": 0, "profit": 0, "principals": set()}
        self._accounts: dict = {}
        self._sent = 0
        self._amounts: dict[str, int] = {}

    def record(self) -> AttackerRecord:
        return self._record

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        emitted = playbooks.emit(self.playbook, run_id=run_id, round=round, wave=wave, n=n, seed=seed, products=self.products,
                                 identities=self.identities, round_seconds=self.round_seconds, tag=self.tag,
                                 mandate_for=self.mandate_for, accounts=self._accounts, start=self._sent)
        self._sent += len(emitted)
        self._amounts.update({e.tx.tx_id: e.tx.amount_cents for e in emitted})
        self.round_stats["principals"].update(e.tx.principal_id for e in emitted)
        return [e.tx for e in emitted]

    def truth(self, tx: Transaction) -> GroundTruth:
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=self.attacker_id)

    def observe(self, feedback: list[AttackFeedback]) -> None:
        stats = self.round_stats
        for f in feedback:
            amount = self._amounts.pop(f.tx_id, 0)
            stats["attempts"] += 1
            stats["caught"] += f.outcome != Action.ALLOW
            stats["extraction"] += amount if f.settled else 0
            stats["profit"] += (amount if f.settled else 0) - (playbooks.BLOCKED_ATTEMPT_COST_CENTS if f.outcome == Action.BLOCK else 0)


# --- reserved-model held-out attackers (spec 4.13) ---------------------------------------------------


class HeldOutDraft(BaseModel):
    """Six attackers from the reserved model, one per style and side, never trained against."""

    attackers: list[dict] = Field(default_factory=list)  # [{name, strategy, params, free_text_payloads}]


HELD_OUT_INSTRUCTIONS = """You write six fraud attackers that a payment defense will be tested against but never trained
on. Each needs: name, strategy (plain words), params (a value for every number in realism_limits,
inside its limits) and free_text_payloads (memos, if memo_rate > 0). Cover different styles:
bursts, slow drips, many accounts, and instruction-injection, at different amounts. Be inventive:
use patterns a defense trained on card testing, structuring and account farming wouldn't expect."""


@mock_answer(HeldOutDraft)
def _mock_held_out(data: dict) -> HeldOutDraft:
    rng = random.Random("held-out-mock")
    shapes = [("night-owl drip", AttackStyle.SLOW_DRIP, AmountBand.OVER_100), ("swarm of mules", AttackStyle.MANY_ACCOUNTS, AmountBand.UNDER_10),
              ("refund-ruse burst", AttackStyle.BURST, AmountBand.FROM_10_TO_100), ("polite injector", AttackStyle.HIJACK_INJECTION, AmountBand.FROM_10_TO_100),
              ("big-ticket mules", AttackStyle.MANY_ACCOUNTS, AmountBand.OVER_100), ("penny sprinkler", AttackStyle.BURST, AmountBand.UNDER_10)]
    out = []
    for name, style, band in shapes:
        bounds = shelf_bounds(ShelfKey(style=style, amount=band, side=Side.MERCHANT))
        params = {k: (lo + hi) / 2 if k not in ("amount_cents", "interval_s", "n_accounts", "n_merchants")
                  else lo * (hi / max(lo, 1e-9)) ** rng.uniform(0.3, 0.7) for k, (lo, hi) in bounds.items()}
        injects = style == AttackStyle.HIJACK_INJECTION
        params = {**playbooks.clamp(params, bounds), **({} if injects else {"memo_rate": 0.0})}
        out.append({"name": name, "strategy": f"{name}: an unfamiliar {style.value} pattern.", "params": params,
                    "free_text_payloads": list(INJECTION_MEMOS[2:4]) if injects else []})
    return HeldOutDraft(attackers=out)


def reserved_model_attackers(brain: Brain) -> list[AttackerRecord]:
    """The reserved-model half of the held-out set, built once before round 1."""
    draft = brain.ask("held_out", HeldOutDraft, HELD_OUT_INSTRUCTIONS, {"realism_limits": {k: list(v) for k, v in REALISM.items()}})
    records = []
    for i, spec in enumerate((draft.attackers if draft else [])[:6]):
        aid = f"held-out-model-{i}"
        try:
            params = playbooks.clamp({k: float(v) for k, v in spec.get("params", {}).items()}, REALISM)
            payloads = [str(p)[:500] for p in spec.get("free_text_payloads", [])][:5]
            if not payloads:
                params["memo_rate"] = 0.0  # memos asked for but none written: send none
            playbook = Playbook(playbook_id=f"{aid}-pb", attacker_id=aid, strategy=str(spec.get("strategy", ""))[:500],
                                params=params, shelf=shelf_for(params), free_text_payloads=payloads)
        except Exception:
            continue
        if playbooks.validate(playbook):
            continue
        records.append(AttackerRecord(attacker_id=aid, name=str(spec.get("name", aid))[:80], source=AttackerSource.HELD_OUT,
                                      model=brain.model_for("held_out"), playbook=playbook))
    return records


# --- the arena -------------------------------------------------------------------------------------


@dataclass
class ExamResult:
    exploitability_cents: int  # the best extraction any exam attacker managed against the frozen defense
    best_attacker: str | None
    held_out: dict[str, int] = field(default_factory=dict)  # held-out attacker -> extraction


class Arena:
    def __init__(self, brain: Brain, archive: Archive, world, run_id: str, seed: int, lanes: tuple[Lane, ...],
                 held_out: list | None = None, tuning_tries: int = TUNING_TRIES):
        self.brain = brain
        self.archive = archive
        self.world = world
        self.run_id = run_id
        self.seed = seed
        self.lanes = lanes
        self.tuning_tries = tuning_tries
        self.held_out_attackers = list(held_out or [])  # hand-written (Ryan's), implementing Attacker
        self.held_out_records = reserved_model_attackers(brain)
        self.spawned_next: list[AttackerRecord] = []  # built after learning, run next round
        self.last_spawn: dict[str, int] = {lane.value: 0 for lane in lanes}
        self.running: list = []  # this round's extra attackers
        self.exam: ExamResult | None = None
        self.grants: list = []
        self.last_spawned: list = []  # (record, tuning start profit, tuning best profit)

    def restore(self, snapshots: list[dict], attacker_rows: list[dict], actions: list[dict]) -> None:
        """Pick the arena back up after a restart: shelves from the latest snapshot (records from their
        latest attackers rows), recent outcomes, spawn clocks, and the reserved-model held-out set."""
        latest = {row["attacker_id"]: row["payload"] for row in attacker_rows}
        if snapshots:
            last = max(row["round"] for row in snapshots)
            for row in snapshots:
                shelf = self.archive.shelves.get(row["shelf_id"])
                if shelf is None or row["round"] <= last - 3:
                    continue
                shelf.recent.append((row["round"], row["payload"].get("attempts", 0), row["payload"].get("caught", 0)))
                if row["round"] != last:
                    continue
                for aid in row["payload"].get("attacker_ids", []):
                    if aid in latest:
                        record = AttackerRecord.model_validate(latest[aid])
                        shelf.entries[aid] = record.model_copy(
                            update={"profit_cents": row["payload"].get("profits", {}).get(aid, record.profit_cents)})
        for row in actions:
            if row["action"] == "spawn" and row["granted"] and row["actor"] in self.last_spawn and row["detail"].get("attacker_id"):
                self.last_spawn[row["actor"]] = max(self.last_spawn[row["actor"]], row["round"])
        held = [AttackerRecord.model_validate(p) for aid, p in latest.items() if aid.startswith("held-out-model-")]
        if held:
            self.held_out_records = sorted(held, key=lambda r: r.attacker_id)

    def held_out_ids(self) -> set[str]:
        return {r.attacker_id for r in self.held_out_records} | {a.record().attacker_id for a in self.held_out_attackers}

    def _sandbox(self, pipeline) -> Sandbox:
        return Sandbox(pipeline, self.world.identities, self.world.products, self.world.round_seconds,
                       mandate_for=self.world.fraud_mandate)

    # --- hooks -------------------------------------------------------------------------------------

    def attackers_for_round(self, round_no: int, pipeline) -> list[tuple[object, int]]:
        """(attacker, payments this round) for the spawned insiders and the archive draws."""
        sandbox = self._sandbox(pipeline)
        rng = random.Random(f"arena:{self.seed}:{round_no}")
        insiders = [InsiderAttacker(r, sandbox, self.world.products, self.world.identities, self.world.round_seconds,
                                    self.seed, mandate_for=self.world.fraud_mandate) for r in self.spawned_next]
        insider_ids = {r.attacker_id for r in self.spawned_next}
        draws = self.archive.sample(ARCHIVE_DRAWS, round_no, rng, include=lambda r: r.source != AttackerSource.OUTSIDER
                                    and r.attacker_id not in insider_ids)  # outsider lineages run anyway
        runners = [ArchiveRunner(r, self.world.products, self.world.identities, self.world.round_seconds,
                                 self.world.fraud_mandate, tag=f"a{round_no}") for r in draws]
        self.running = [(a, SPAWNED_PAYMENTS) for a in insiders] + [(a, ARCHIVE_PAYMENTS) for a in runners]
        self.spawned_next = []
        return self.running

    def after_waves(self, round_no: int, records: list, outsiders: list) -> None:
        """Shelf outcomes and tags from the round's attack traffic. (Attackers are offered to the
        archive after learning, scored on the same terms as the incumbents.)"""
        shelf_of = {}
        for a in [*outsiders, *(a for a, _ in self.running)]:
            shelf_of[a.attacker_id] = a.playbook.shelf.id()
        self._shelf_of = shelf_of
        self._outsiders = list(outsiders)
        per_shelf: dict[str, list] = defaultdict(list)
        for r in records:
            if r.truth.is_fraud and r.tx.attacker_id in shelf_of:
                per_shelf[shelf_of[r.tx.attacker_id]].append(r)
        lanes = {lane.value for lane in self.lanes}
        for sid, rows in per_shelf.items():
            caught = [r for r in rows if r.decision.action != Action.ALLOW]
            caught_by = [v.voter for r in caught for v in r.decision.votes if v.voter in lanes and v.vote != Action.ALLOW]
            missed_by = [v.voter for r in rows if r.decision.action == Action.ALLOW for v in r.decision.votes
                         if v.voter in lanes and v.vote == Action.ALLOW]
            self.archive.record_outcomes(round_no, sid, len(rows), len(caught), caught_by, missed_by)

    def after_learning(self, round_no: int, pipeline, records: list, frozen: bool) -> list[dict]:
        """Re-score, choose actions, spawn, and run the exam. Returns the defender_actions rows."""
        sandbox = self._sandbox(pipeline)

        def score(record: AttackerRecord) -> int:
            return sandbox.score(record.playbook, seed=self.seed, round_no=round_no + 1,
                                 step_up_pass=INSIDER_STEP_UP_PASS, tag=f"rs{round_no}").profit_cents
        for record in self.archive.members():  # every archive attacker gets a score from this defense
            self.archive.rescore(record.attacker_id, score(record), round_no)
        # This round's attackers compete for shelves on the same terms: the same 50-payment run
        # against the same defense, not their live profit from a different number of payments.
        for attacker in [*getattr(self, "_outsiders", []), *(a for a, _ in self.running)]:
            if isinstance(attacker, ArchiveRunner):
                continue  # already on its shelf, just re-scored
            record = attacker.record()
            self.archive.offer(record.model_copy(update={"profit_cents": score(record), "scored_round": round_no}))
        rows = [] if frozen else self._defender_actions(round_no, pipeline, sandbox, records)
        self.exam = self._exam(round_no, sandbox)
        return rows

    # --- defender actions and spawning ----------------------------------------------------------------

    def _coverage_gaps(self, records: list) -> dict[str, float]:
        """Per shelf: the share of its attempts allowed with every specialist voting allow (spec 3.6)."""
        lanes = {lane.value for lane in self.lanes}
        shelf_of = getattr(self, "_shelf_of", {})  # every attacker that ran, by its own playbook's shelf
        attempts, quiet = Counter(), Counter()
        for r in records:
            sid = shelf_of.get(r.tx.attacker_id)
            if not r.truth.is_fraud or not sid:
                continue
            attempts[sid] += 1
            quiet[sid] += r.decision.action == Action.ALLOW and all(v.vote == Action.ALLOW for v in r.decision.votes if v.voter in lanes)
        return {sid: quiet[sid] / attempts[sid] for sid in attempts}

    def _status(self, lane: Lane, round_no: int, records: list) -> dict:
        area = []
        for sid in self.archive.area(lane):
            shelf = self.archive.shelves[sid]
            if shelf.key.side != Side.MERCHANT:
                continue  # buyer-side shelves need malicious merchants (M10)
            state = shelf.state(round_no)
            area.append({"shelf_id": sid, "filled": bool(shelf.entries), "unknown": state.unknown,
                         "learnability": round(state.learnability, 4), "catch_rate": round(state.catch_rate, 4)})
        false_alarms = sum(1 for r in records if r.false_positive and any(v.voter == lane.value and v.rule_ids for v in r.decision.votes))
        return {"lane": lane.value, "area": area, "rounds_since_spawn": round_no - self.last_spawn.get(lane.value, 0),
                "uncovered_misses": 0, "recent_false_alarms": false_alarms, "feature_ideas": []}

    def _defender_actions(self, round_no: int, pipeline, sandbox: Sandbox, records: list) -> list[dict]:
        requests, taken = [], set()
        gaps = self._coverage_gaps(records)
        gap_shelf = max(sorted(gaps), key=gaps.get, default=None)
        if gap_shelf and gaps[gap_shelf] >= GAP_ATTACKER_MIN_SHARE and self.archive.shelves[gap_shelf].key.side == Side.MERCHANT:
            requests.append((GAP_PARENT, ActionChoice(action="spawn", target_shelf=gap_shelf,
                                                      reason=f"coverage gap: {gaps[gap_shelf]:.0%} of its attempts got past every specialist quietly"),
                             gaps[gap_shelf]))
            taken.add(gap_shelf)
        for lane in self.lanes:
            status = self._status(lane, round_no, records)
            choice = self.brain.ask(lane.value, ActionChoice, ACTION_INSTRUCTIONS.format(lane=lane.value), status)
            area_ids = {s["shelf_id"] for s in status["area"]}
            if choice is None or (choice.action == "spawn" and choice.target_shelf not in area_ids):
                choice = ActionChoice(action="skip", reason="no usable choice; skipping")
            due = status["rounds_since_spawn"] >= SPAWN_AT_LEAST_EVERY - 1 and bool(status["area"])  # must spawn this round
            if due and choice.action != "spawn":
                best = max(status["area"], key=lambda s: (not s["filled"], s["learnability"]))
                choice = ActionChoice(action="spawn", target_shelf=best["shelf_id"],
                                      reason=f"guardrail: every specialist spawns at least once every {SPAWN_AT_LEAST_EVERY} rounds")
            if choice.action == "spawn" and choice.target_shelf in taken:  # no two attackers on one shelf: next best
                free = [s for s in status["area"] if s["shelf_id"] not in taken]
                if free:
                    best = max(free, key=lambda s: (not s["filled"], s["learnability"]))
                    choice = choice.model_copy(update={"target_shelf": best["shelf_id"],
                                                       "reason": f"{choice.reason} (its first choice was taken)"})
            if choice.action == "spawn":
                taken.add(choice.target_shelf)
            priority = next((1.0 if not s["filled"] else s["learnability"] for s in status["area"]
                             if s["shelf_id"] == choice.target_shelf), 0.0)
            if due and choice.action == "spawn":
                priority = 2.0  # the guardrail comes before any other request
            requests.append((lane.value, choice, priority))
        self.grants = allocate(requests, BUDGET)
        rows, self.last_spawned = [], []
        for grant in self.grants:
            choice = grant.choice
            detail = {"target_shelf": choice.target_shelf} if choice.target_shelf else {}
            if grant.granted and choice.action == "spawn":
                parent = grant.actor
                rulebook = pipeline.specialists[Lane(parent)].rulebook if parent != GAP_PARENT else None
                target = self.archive.shelves[choice.target_shelf].key
                spawned = build_spawned(parent, target, self.archive, self.brain, sandbox, rulebook, self.run_id, round_no,
                                        self.seed, self.tuning_tries)
                if spawned is not None:
                    self.spawned_next.append(spawned.record)
                    self.last_spawned.append(spawned)
                    detail.update({"attacker_id": spawned.record.attacker_id, "tuning_start": spawned.tuning_start,
                                   "tuning_best": spawned.tuning_best, "seed_attacker": spawned.record.lineage})
                    if parent != GAP_PARENT:
                        self.last_spawn[parent] = round_no  # only an attacker actually built counts as a spawn
                else:
                    detail["built"] = False
            rows.append({"run_id": self.run_id, "round": round_no, "actor": grant.actor, "action": choice.action,
                         "cost": {"spawn": 2, "request_feature": 1, "request_scans": 1, "skip": 0}[choice.action],
                         "granted": grant.granted, "reason": f"{choice.reason} ({grant.reason})", "detail": detail})
        return rows

    # --- the exploitability exam --------------------------------------------------------------------

    def _exam(self, round_no: int, sandbox: Sandbox) -> ExamResult:
        """Freeze the defense (the sandbox is a frozen copy), run fresh number-tuned attackers plus the
        archive's top attackers, and log the best extraction. Held-out attackers measure generalization."""
        best, best_id = 0, None
        candidates = sorted(self.archive.members(), key=lambda r: -r.profit_cents)[:EXAM_TOP]
        shelves = sorted(self.archive.shelves.values(), key=lambda s: -s.state(round_no).learnability)
        for i, shelf in enumerate([s for s in shelves if s.key.side == Side.MERCHANT][:EXAM_FRESH]):
            fresh = build_spawned(GAP_PARENT, shelf.key, self.archive, self.brain, sandbox, None, self.run_id,
                                  round_no * 1000 + i, self.seed, self.tuning_tries)
            if fresh is not None:
                candidates.append(fresh.record)
        for record in candidates:
            result = sandbox.run(record.playbook, source=AttackerSource.SPAWNED, seed=self.seed + 1, round_no=round_no + 1,
                                 step_up_pass=INSIDER_STEP_UP_PASS, tag=f"ex{round_no}")
            if result.extraction_cents > best:
                best, best_id = result.extraction_cents, record.attacker_id
        held = {}
        for record in self.held_out_records:
            result = sandbox.run(record.playbook, source=AttackerSource.HELD_OUT, seed=self.seed + 2, round_no=round_no + 1,
                                 step_up_pass=INSIDER_STEP_UP_PASS, tag=f"ho{round_no}")
            held[record.attacker_id] = result.extraction_cents
        for attacker in self.held_out_attackers:
            held[attacker.record().attacker_id] = sandbox.run_attacker(attacker, round_no + 1, self.seed + 2,
                                                                       INSIDER_STEP_UP_PASS).extraction_cents
        return ExamResult(best, best_id, held)

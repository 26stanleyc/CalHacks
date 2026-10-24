"""Defender-chosen actions and archive-aimed spawning (spec 3.9 points 3 and 6, 3.6; M7).

Each round, each specialist's model sees a short status of its area and picks one action from a
shared budget the orchestrator holds:

| Action          | Cost | When it makes sense                                    |
|-----------------|------|--------------------------------------------------------|
| spawn           | 2    | a shaky or empty shelf in its area                     |
| request_feature | 1    | its misses show a signal its lane can't express yet    |
| request_scans   | 1    | merchants whose buyers keep breaching mandates (M10)  |
| skip            | 0    | its area is mostly green                               |

The orchestrator allocates when requests exceed the budget: shakiest areas first, then empty
shelves, then features and scans. Guardrails: every specialist spawns at least once every 3 rounds,
no two spawned attackers target the same shelf in a round, new features pass the lane check, and
page scans are capped at 10 per round. The orchestrator's gap attacker comes out of the same
budget, aimed at the shelf where the coverage map shows the most fraud getting past every
specialist quietly.

A spawned attacker (an insider) is built on a model from the other family than its parent's, seeded
from one of its target shelf's top 3 attackers, given its parent's rulebook gray-box (rule names and
noisy thresholds), and number-tuned in the sandbox before it runs.
"""

import random
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from contracts.config import MODEL_ROLES
from contracts.schemas import (Action, AmountBand, AttackerRecord, AttackerSource, AttackFeedback, AttackStyle,
                               GroundTruth, IdentityStatus, Lane, Playbook, ShelfKey, Side, Transaction)
from engine.arena import playbooks
from engine.arena.archive import Archive
from engine.arena.outsiders import shelf_for
from engine.arena.playbooks import REALISM, WAVES
from engine.arena.sandbox import Sandbox, tune
from engine.brains import Brain, mock_answer
from engine.sim.world import INJECTION_MEMOS

BUDGET = 8
COSTS = {"spawn": 2, "request_feature": 1, "request_scans": 1, "skip": 0}
SPAWN_AT_LEAST_EVERY = 3
MAX_SCANS = 10
GAP_ATTACKER_MIN_SHARE = 0.2  # coverage gap worth a gap attacker
GAP_PARENT = "orchestrator"
GAP_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
INSIDER_STEP_UP_PASS = 0.20
INSIDER_RETUNE_TRIES = 30


def spawn_role(parent: str) -> str:
    """The model role a parent's attackers are built with (contracts.config: the other family, spec 2.1)."""
    return f"spawn_{parent}"


def spawn_model(parent: str) -> str:
    return MODEL_ROLES[spawn_role(parent)]


# --- the action choice ------------------------------------------------------------------------------


class FeatureRequest(BaseModel):
    entity: str
    field: str
    aggregate: str
    window_s: int


class ActionChoice(BaseModel):
    action: Literal["spawn", "request_feature", "request_scans", "skip"]
    reason: str
    target_shelf: str | None = None
    feature: FeatureRequest | None = None
    merchants: list[str] = Field(default_factory=list)


ACTION_INSTRUCTIONS = """You are the {lane} specialist in a self-red-teaming fraud defense. Each round you choose ONE
action from a shared budget (spawn costs 2, request_feature 1, request_scans 1, skip 0):
- spawn: build an attacker aimed at one shelf in your area (target_shelf = a shelf id from your status)
- request_feature: ask for a new feature composed from primitives (entity x field x aggregate x window_s)
- request_scans: ask for page scans of merchants (merchants = their ids)
- skip: your area is mostly green
Shaky shelves (learnability near 0.25) and empty shelves teach the most. Give a one-line reason."""


@mock_answer(ActionChoice)
def _mock_choice(data: dict) -> ActionChoice:
    shelves = data["area"]
    known = [s for s in shelves if s["filled"] and not s["unknown"]]
    shaky = max(known, key=lambda s: s["learnability"], default=None)
    empty = [s for s in shelves if not s["filled"]]
    if data["rounds_since_spawn"] >= SPAWN_AT_LEAST_EVERY - 1 or (shaky and shaky["learnability"] >= 0.15):
        target = shaky["shelf_id"] if shaky else (empty[0]["shelf_id"] if empty else shelves[0]["shelf_id"])
        return ActionChoice(action="spawn", target_shelf=target, reason="mock: a shaky shelf in my area")
    if empty:
        return ActionChoice(action="spawn", target_shelf=empty[0]["shelf_id"], reason="mock: explore an empty shelf")
    if data["uncovered_misses"] > 0 and data["feature_ideas"]:
        return ActionChoice(action="request_feature", feature=FeatureRequest(**data["feature_ideas"][0]),
                            reason="mock: misses my lane can't express yet")
    return ActionChoice(action="skip", reason="mock: my area is green")


@dataclass
class Grant:
    actor: str  # lane name or "orchestrator"
    choice: ActionChoice
    granted: bool
    reason: str


def allocate(requests: list[tuple[str, ActionChoice, float]], budget: int = BUDGET) -> list[Grant]:
    """requests: (actor, choice, priority) where priority is the target shelf's learnability (or 1.0
    for an empty shelf it explores). Shakiest areas first, then empty shelves, then features and scans."""
    def order(item):
        actor, choice, priority = item
        kind = {"spawn": 0, "request_feature": 1, "request_scans": 2, "skip": 3}[choice.action]
        return kind, -priority, actor
    grants, left, taken, scans = [], budget, set(), 0
    for actor, choice, _ in sorted(requests, key=order):
        cost = COSTS[choice.action]
        if choice.action == "skip":
            grants.append(Grant(actor, choice, True, "skipped"))
        elif choice.action == "spawn" and choice.target_shelf in taken:
            grants.append(Grant(actor, choice, False, "another attacker already targets that shelf this round"))
        elif choice.action == "request_scans" and scans + len(choice.merchants) > MAX_SCANS:
            grants.append(Grant(actor, choice, False, f"page scans are capped at {MAX_SCANS} per round"))
        elif cost > left:
            grants.append(Grant(actor, choice, False, f"over budget: {left} points left, needs {cost}"))
        else:
            left -= cost
            taken.add(choice.target_shelf)
            scans += len(choice.merchants)
            grants.append(Grant(actor, choice, True, f"granted ({left} points left)"))
    return grants


# --- building a spawned attacker ------------------------------------------------------------------------


class SpawnDraft(BaseModel):
    strategy: str
    params: dict[str, float]
    param_bounds: dict[str, tuple[float, float]] = Field(default_factory=dict)
    free_text_payloads: list[str] = Field(default_factory=list)
    reasoning: str = ""


SPAWN_INSTRUCTIONS = """You build an attacker to test a fraud defense from the inside. Your parent is the {parent}
specialist: you can see its rule names and rough thresholds, and your job is to get fraud past the
WHOLE defense, not just your parent. Target shelf: style {style}, amount {amount}, side {side}.
Write a playbook: a strategy in plain words, a starting value and an allowed [low, high] range for
every number in realism_limits (inside those limits), and memos if memo_rate > 0. A cheap optimizer
will tune the numbers against a frozen copy of the defense afterwards."""

STYLE_DEFAULTS = {
    AttackStyle.BURST: {"interval_s": 6.0, "n_accounts": 1.0, "n_merchants": 8.0, "memo_rate": 0.0},
    AttackStyle.SLOW_DRIP: {"interval_s": 900.0, "n_accounts": 1.0, "n_merchants": 4.0, "memo_rate": 0.0},
    AttackStyle.MANY_ACCOUNTS: {"interval_s": 40.0, "n_accounts": 20.0, "n_merchants": 2.0, "memo_rate": 0.0},
    AttackStyle.HIJACK_INJECTION: {"interval_s": 300.0, "n_accounts": 2.0, "n_merchants": 4.0, "memo_rate": 1.0},
}
AMOUNT_DEFAULTS = {AmountBand.UNDER_10: 400.0, AmountBand.FROM_10_TO_100: 5_000.0, AmountBand.OVER_100: 20_000.0}
AMOUNT_BOUNDS = {AmountBand.UNDER_10: (50.0, 999.0), AmountBand.FROM_10_TO_100: (1_000.0, 10_000.0),
                 AmountBand.OVER_100: (10_001.0, 50_000.0)}
STYLE_BOUNDS = {
    AttackStyle.BURST: {"interval_s": (1.0, 60.0), "n_accounts": (1.0, 7.0), "memo_rate": (0.0, 0.29)},
    AttackStyle.SLOW_DRIP: {"interval_s": (61.0, 3_000.0), "n_accounts": (1.0, 7.0), "memo_rate": (0.0, 0.29)},
    AttackStyle.MANY_ACCOUNTS: {"n_accounts": (8.0, 40.0), "memo_rate": (0.0, 0.29)},
    AttackStyle.HIJACK_INJECTION: {"memo_rate": (0.3, 1.0)},
}


def shelf_bounds(shelf: ShelfKey) -> dict[str, tuple[float, float]]:
    """Bounds that keep a tuned attacker on its target shelf."""
    bounds = {name: tuple(limits) for name, limits in REALISM.items()}
    bounds["amount_cents"] = AMOUNT_BOUNDS[shelf.amount]
    bounds.update(STYLE_BOUNDS[shelf.style])
    return bounds


@mock_answer(SpawnDraft)
def _mock_spawn(data: dict) -> SpawnDraft:
    """Start from the seed attacker if there is one, else from the shelf's typical numbers, and
    leave the hard part to the tuner."""
    shelf = ShelfKey.model_validate(data["target_shelf"])
    params = dict(data["seed_playbook"]["params"]) if data.get("seed_playbook") else {
        **{"amount_spread": 0.3, "identity_quality": 0.3}, **STYLE_DEFAULTS[shelf.style],
        "amount_cents": AMOUNT_DEFAULTS[shelf.amount]}
    bounds = shelf_bounds(shelf)
    params = playbooks.clamp(params, bounds)
    payloads = list(data["seed_playbook"]["free_text_payloads"]) if data.get("seed_playbook") else []
    if params["memo_rate"] > 0 and not payloads:
        payloads = list(INJECTION_MEMOS[:2])
    return SpawnDraft(strategy=f"Get {shelf.style.value} fraud of {shelf.amount.value} past the defense, "
                               f"avoiding the parent's rules.", params=params,
                      param_bounds={k: list(v) for k, v in bounds.items()}, free_text_payloads=payloads,
                      reasoning="mock spawner: the shelf's typical numbers; the tuner does the rest")


def gray_box(rulebook, rng: random.Random) -> list[dict]:
    """The parent's rules as a spawned attacker sees them: names and thresholds with noise (spec 3.5)."""
    def noisy(cond):
        if "feature" in cond:
            value = cond["value"]
            return {**cond, "value": round(value * rng.uniform(0.85, 1.15), 3) if value else value}
        (key, inner), = cond.items()
        return {key: noisy(inner) if key == "not" else [noisy(c) for c in inner]}
    return [{"id": r.id, "when": noisy(r.when), "description": r.description} for r in rulebook.rules]


@dataclass
class Spawned:
    record: AttackerRecord
    tuning_start: int
    tuning_best: int


class InsiderAttacker:
    """A spawned attacker running in this round's traffic (implements contracts.interfaces.Attacker).
    Between waves it sees outcomes plus which specialist objected, and re-tunes in its sandbox."""

    def __init__(self, record: AttackerRecord, sandbox: Sandbox, products: list, identities: dict[str, IdentityStatus],
                 round_seconds: float, seed: int, mandate_for: playbooks.MandateFor = playbooks.self_mandate):
        self._record = record
        self.attacker_id = record.attacker_id
        self.playbook = record.playbook
        self.sandbox = sandbox
        self.products = products
        self.identities = identities
        self.round_seconds = round_seconds
        self.seed = seed
        self.mandate_for = mandate_for
        self.step_up_pass = INSIDER_STEP_UP_PASS
        self.round_stats = {"extraction": 0, "attempts": 0, "caught": 0, "gross": 0, "profit": 0, "principals": set()}
        self.objections: dict[str, int] = {}
        self.wave_logs: list = []
        self._sent: dict[str, int] = {}
        self._where = (0, 0)
        self.retunes = 0
        self._accounts: dict = {}
        self._payments = 0

    def record(self) -> AttackerRecord:
        return self._record.model_copy(update={"playbook": self.playbook, "profit_cents": self.round_stats["profit"],
                                               "scored_round": self._where[0] or None})

    def generate(self, run_id: str, round: int, wave: int, n: int, seed: int) -> list[Transaction]:
        self._where = (round, wave)
        emitted = playbooks.emit(self.playbook, run_id=run_id, round=round, wave=wave, n=n, seed=seed,
                                 products=self.products, identities=self.identities, round_seconds=self.round_seconds,
                                 mandate_for=self.mandate_for, accounts=self._accounts, start=self._payments)
        self._payments += len(emitted)
        self._sent.update({e.tx.tx_id: e.tx.amount_cents for e in emitted})
        self.round_stats["principals"].update(e.tx.principal_id for e in emitted)
        return [e.tx for e in emitted]

    def truth(self, tx: Transaction) -> GroundTruth:
        return GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=self.attacker_id)

    def observe(self, feedback: list[AttackFeedback]) -> None:
        rows = []
        for f in feedback:
            amount = self._sent.pop(f.tx_id, None)
            if amount is None:
                continue
            rows.append((amount, bool(f.settled), f.outcome.value))
            for lane in f.objected_by or []:
                self.objections[lane.value] = self.objections.get(lane.value, 0) + 1
        stats = self.round_stats
        stats["attempts"] += len(rows)
        stats["caught"] += sum(o != Action.ALLOW.value for _, _, o in rows)
        stats["extraction"] += sum(a for a, s, _ in rows if s)
        stats["gross"] += playbooks.profit_cents([a for a, _, _ in rows], [s for _, s, _ in rows], [o for _, _, o in rows], 0)
        stats["profit"] = stats["gross"] - sum(playbooks.IDENTITY_COST_CENTS[self.identities[p]] for p in stats["principals"])
        caught = sum(o != Action.ALLOW.value for _, _, o in rows)
        if rows and caught > len(rows) / 2 and self._where[1] < WAVES - 1:  # a bad wave with waves left: re-tune
            rng = random.Random(f"{self.attacker_id}:retune:{self._where}")
            tuning = tune(self.playbook, lambda pb: self.sandbox.run(pb, source=AttackerSource.SPAWNED, seed=self.seed,
                                                                       round_no=self._where[0], step_up_pass=self.step_up_pass).profit_cents,
                          rng, tries=INSIDER_RETUNE_TRIES)
            if tuning.best is not self.playbook:
                self.retunes += 1
                self.playbook = tuning.best.model_copy(update={"playbook_id": f"{self.attacker_id}-t{self.retunes}"})


def build_spawned(parent: str, target: ShelfKey, archive: Archive, brain: Brain, sandbox: Sandbox, rulebook,
                  run_id: str, round_no: int, seed: int, tries: int) -> Spawned | None:
    """One spawned attacker for next round: seed, intel, other-family model, then tuning in the sandbox."""
    rng = random.Random(f"spawn:{seed}:{round_no}:{parent}:{target.id()}")
    shelf = archive.shelves[target.id()]
    seed_record = shelf.entries[rng.choice(shelf.ranked())] if shelf.entries else None
    data = {"parent": parent, "target_shelf": target.model_dump(mode="json"),
            "seed_playbook": seed_record.playbook.model_dump(mode="json") if seed_record else None,
            "parent_rules": gray_box(rulebook, rng) if rulebook is not None else [],
            "realism_limits": {k: list(v) for k, v in REALISM.items()}}
    draft = brain.ask(spawn_role(parent), SpawnDraft, SPAWN_INSTRUCTIONS.format(
        parent=parent, style=target.style.value, amount=target.amount.value, side=target.side.value), data)
    if draft is None:
        return None
    attacker_id = f"spawned-r{round_no}-{parent}-{target.style.value}-{target.amount.value}"
    # The tuner may only search numbers that keep the attacker on its target shelf: the draft's bounds,
    # intersected with the shelf's.
    on_shelf = shelf_bounds(target)
    bounds = {}
    for name, (lo, hi) in on_shelf.items():
        given = draft.param_bounds.get(name)
        if given is not None and len(given) == 2:
            lo, hi = max(lo, float(given[0])), min(hi, float(given[1]))
        bounds[name] = (lo, hi) if lo <= hi else on_shelf[name]
    try:
        params = playbooks.clamp({k: float(v) for k, v in draft.params.items() if k in REALISM}, bounds)
        playbook = Playbook(playbook_id=f"{attacker_id}-pb0", attacker_id=attacker_id, strategy=str(draft.strategy)[:500],
                            params=params, param_bounds=bounds, shelf=target,
                            free_text_payloads=[str(p)[:500] for p in draft.free_text_payloads][:5])
    except Exception:
        return None
    if not playbook.free_text_payloads and playbook.params["memo_rate"] > 0:
        return None
    if playbooks.validate(playbook):
        return None
    tuning = tune(playbook, lambda pb: sandbox.run(pb, source=AttackerSource.SPAWNED, seed=seed, round_no=round_no + 1,
                                                    step_up_pass=INSIDER_STEP_UP_PASS).profit_cents, rng, tries=tries)
    best = tuning.best.model_copy(update={"playbook_id": f"{attacker_id}-tuned", "shelf": target})
    record = AttackerRecord(attacker_id=attacker_id, name=f"{parent}'s {target.style.value.replace('_', ' ')} attacker",
                            source=AttackerSource.SPAWNED, model=spawn_model(parent), parent=parent,
                            lineage=seed_record.attacker_id if seed_record else None, generation=round_no,
                            playbook=best, profit_cents=tuning.best_profit, scored_round=round_no)
    return Spawned(record, tuning.start_profit, tuning.best_profit)

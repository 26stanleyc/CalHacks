"""The sandbox and the number tuner (spec 3.9 points 1 and 5, 4.12-13; M7).

`Sandbox` is a frozen copy of the current defense: the deployed rulebooks, weights and voters, over
fresh feature state. Insiders tune against it, archive attackers are re-scored in it, and the
exploitability exam runs in it. Nothing run here reaches the live defense, its labels or its
metrics. Outsider lineages never get one (they tune from their own live outcomes only).

`tune` is the cheap optimizer: random search over a playbook's numbers within its bounds, about 200
tries of ~50 payments each, keeping the most profitable. The starting playbook is always tried
first, so a tuned attacker never scores below its untuned start. Every try is checked against the
realism limits before it runs.
"""

import random
from collections import Counter
from dataclasses import dataclass, field

from contracts.schemas import Action, AttackerSource, IdentityStatus, Lane, Playbook
from engine.arena import playbooks
from engine.arena.playbooks import REALISM, WAVES
from engine.orchestrator import Orchestrator
from engine.pipeline import DefensePipeline

SANDBOX_PAYMENTS = 50
TUNING_TRIES = 200
SCALE_PARAMS = ("amount_cents", "interval_s", "n_accounts", "n_merchants")


@dataclass
class SandboxResult:
    profit_cents: int
    attempts: int
    caught: int
    extraction_cents: int
    objected_by: Counter = field(default_factory=Counter)  # lane -> payments it objected to
    all_quiet_allowed: int = 0  # allowed payments where every specialist voted allow (coverage gaps)


class Sandbox:
    def __init__(self, pipeline: DefensePipeline, identities: dict[str, IdentityStatus], products: list,
                 round_seconds: float, mandate_for: playbooks.MandateFor = playbooks.self_mandate):
        self.lanes = pipeline.lanes
        self.catalog = pipeline.catalog
        self.rulebooks = {lane: s.rulebook for lane, s in pipeline.specialists.items()}
        self.costs = pipeline.orchestrator.costs
        self.weights = dict(pipeline.orchestrator.weights)
        self.voters = list(pipeline.voters)
        self.product_text = pipeline.features.product_text
        self.identities = dict(identities)
        self.products = products
        self.round_seconds = round_seconds
        self.mandate_for = mandate_for
        self.runs = 0

    def score(self, playbook: Playbook, *, seed: int, round_no: int, step_up_pass: float, tag: str) -> SandboxResult:
        """The defense measuring an archive attacker (re-scoring, archive entry): its committed playbook
        against this frozen copy. Not tuning, so outsiders are measured too; they never call this."""
        return self.run(playbook, source=AttackerSource.SPAWNED, seed=seed, round_no=round_no, step_up_pass=step_up_pass, tag=tag)

    def run(self, playbook: Playbook, *, source: AttackerSource, seed: int, round_no: int, step_up_pass: float,
            n: int = SANDBOX_PAYMENTS, tag: str = "sb") -> SandboxResult:
        if source == AttackerSource.OUTSIDER:
            raise PermissionError("outsider lineages never get a sandbox; they tune from live outcomes only")
        problems = playbooks.validate(playbook)
        if problems:
            raise ValueError(f"playbook outside its limits: {problems}")
        from engine.engine import resolve  # here, not at import time: engine.engine imports the arena

        self.runs += 1
        identities = dict(self.identities)
        pipeline = DefensePipeline(self.lanes, self.catalog, self.rulebooks, identities,
                                   Orchestrator(costs=self.costs, weights=dict(self.weights)), voters=self.voters,
                                   product_text=self.product_text)
        txs, accounts = [], {}
        per_wave = [n // WAVES + (w < n % WAVES) for w in range(WAVES)]
        for wave, count in enumerate(per_wave):
            txs += playbooks.emit(playbook, run_id="sandbox", round=round_no, wave=wave, n=count, seed=seed,
                                  products=self.products, identities=identities, round_seconds=self.round_seconds,
                                  tag=tag, mandate_for=self.mandate_for, accounts=accounts, start=len(txs))
        result = SandboxResult(0, 0, 0, 0)
        amounts, settled, outcomes, principals = [], [], [], set()
        for emitted in sorted(txs, key=lambda e: (e.tx.ts, e.tx.tx_id)):
            decision, _ = pipeline.decide(emitted.tx)
            outcome = resolve(emitted.tx, emitted.truth, decision, step_up_pass, self.costs.legit_step_up_abandon)
            if outcome.settled:
                pipeline.features.record_spend(emitted.tx)
            amounts.append(emitted.tx.amount_cents)
            settled.append(outcome.settled)
            outcomes.append(decision.action.value)
            principals.add(emitted.tx.principal_id)
            result.attempts += 1
            result.caught += decision.action != Action.ALLOW
            for vote in decision.votes:
                if vote.vote != Action.ALLOW and vote.voter in {lane.value for lane in self.lanes}:
                    result.objected_by[vote.voter] += 1
            if decision.action == Action.ALLOW and all(v.vote == Action.ALLOW for v in decision.votes):
                result.all_quiet_allowed += 1
        identity_cost = sum(playbooks.IDENTITY_COST_CENTS[identities[p]] for p in principals)
        result.extraction_cents = sum(a for a, s in zip(amounts, settled) if s)
        result.profit_cents = playbooks.profit_cents(amounts, settled, outcomes, identity_cost)
        return result


    def run_attacker(self, attacker, round_no: int, seed: int, step_up_pass: float,
                     n: int = SANDBOX_PAYMENTS) -> SandboxResult:
        """Run any contracts.interfaces.Attacker (e.g. Ryan's hand-written held-out ones) against the
        frozen defense: its own transactions and ground truth, three waves' worth."""
        from engine.engine import resolve

        self.runs += 1
        pipeline = DefensePipeline(self.lanes, self.catalog, self.rulebooks, dict(self.identities),
                                   Orchestrator(costs=self.costs, weights=dict(self.weights)), voters=self.voters,
                                   product_text=self.product_text)
        txs = []
        for wave in range(WAVES):
            txs += attacker.generate("sandbox", round_no, wave, n // WAVES + (wave < n % WAVES), seed)
        result = SandboxResult(0, 0, 0, 0)
        for tx in sorted(txs, key=lambda t: (t.ts, t.tx_id)):
            truth = attacker.truth(tx)
            decision, _ = pipeline.decide(tx)
            outcome = resolve(tx, truth, decision, step_up_pass, self.costs.legit_step_up_abandon)
            if outcome.settled:
                pipeline.features.record_spend(tx)
                result.extraction_cents += tx.amount_cents
            result.attempts += 1
            result.caught += decision.action != Action.ALLOW
        result.profit_cents = result.extraction_cents
        return result


def perturb(playbook: Playbook, rng: random.Random, local: bool) -> Playbook:
    """A nearby playbook (local) or a uniformly random one, always inside the playbook's bounds."""
    bounds = playbooks.bounds_of(playbook)
    params = dict(playbook.params)
    names = [n for n in REALISM if n != "memo_rate" or playbook.free_text_payloads]
    for name in (rng.sample(names, rng.randint(1, 3)) if local else names):
        lo, hi = bounds[name]
        if not local:
            params[name] = rng.uniform(lo, hi) if name not in SCALE_PARAMS else lo * (hi / max(lo, 1e-9)) ** rng.random()
        elif name in SCALE_PARAMS:
            params[name] = params[name] * 2 ** rng.gauss(0, 0.5)
        else:
            params[name] = params[name] + rng.gauss(0, 0.1) * (hi - lo)
    return playbook.model_copy(update={"params": playbooks.clamp(params, bounds)})


@dataclass
class Tuning:
    best: Playbook
    best_profit: int
    start_profit: int
    tries: int
    history: list[tuple[dict, int]] = field(default_factory=list)


def tune(playbook: Playbook, score, rng: random.Random, tries: int = TUNING_TRIES) -> Tuning:
    """Random search: the start first, then 70% local moves around the best so far, 30% uniform."""
    start = score(playbook)
    best, best_profit = playbook, start
    history = [(dict(playbook.params), start)]
    for _ in range(tries - 1):
        candidate = perturb(best, rng, local=rng.random() < 0.7)
        assert playbooks.validate(candidate) == [], "a tuning try left its realism limits"
        profit = score(candidate)
        history.append((dict(candidate.params), profit))
        if profit > best_profit:
            best, best_profit = candidate, profit
    return Tuning(best, best_profit, start, tries, history)

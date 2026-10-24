"""FraudEngine: the real implementation of contracts.entrypoints.Engine (spec 3.7).

Two transports with identical decisions:
- "inprocess" (default): calls the DefensePipeline directly. Fast; used by tests, benchmarks
  and precompute.
- "bureau": every decision travels through the uAgents Bureau (ingress -> orchestrator ->
  specialists), as in the live demo.

Rounds are the training path: their payments update the defense's state and are logged under
the engine's run. check_payment is the outside path (dashboard what-ifs, chat agents): it scores
for real but never changes the defense's state, and it logs under a separate "-checks" run with
engine-made ids, so callers can use any run_id or reuse a tx_id without breaking the database.

The engine is stateful and FastAPI serves it from a thread pool, so every entry point that
touches state runs under one lock.

A round (spec 3.9 point 7, M3) runs in 3 waves. Each wave is that slice of the legitimate
traffic plus each outsider lineage's payments. The defense stays fixed for the whole round.
Between waves the outsiders see only their outcomes and adapt. After the waves, unless the defense
is frozen, the learning loop runs (spec 4, M4).

Locking: `_round_lock` lets one round run at a time; `_lock` guards the defense's state and is held
only while a wave's payments are decided or the defense changes, so model calls (attackers between
waves, reflection, the critic) never block check_payment or explain.

The investigator (S5), the arena (S6) and mandate scoring (S7) plug in later.
"""

import hashlib
import threading
from collections import defaultdict
from dataclasses import dataclass

from contracts.api import team_allowlist
from contracts.entrypoints import NotAllowed
from contracts.interfaces import PageScanner, Settlement
from contracts.schemas import (Action, AttackerRecord, AttackFeedback, Decision, FeatureVector, GroundTruth, Mandate,
                               PageScan, RoundMetrics,
                               ThreatIntelItem, Transaction)
from contracts.stubs import StubPageScanner, StubSettlement
from engine.arena.outsiders import PERSONAS, OutsiderAttacker
from engine.arena.playbooks import WAVES
from engine.brains import Brain
from engine.learning.labels import Record, realized_cost
from engine.learning.loop import LearningLoop
from engine.pipeline import DefensePipeline
from engine.sim.world import World
from engine.store import LogStore, MemoryStore, log_bundle

CHECK_PREFIX = "check:"
ROUND_BUDGET_USD = 0.60  # spec 4.1's per-round estimate; a round over twice this pauses the run
FP_CAP = 0.02


class LLMBudgetExceeded(RuntimeError):
    """A round cost more than twice the estimate (spec 4.1 stop rule); the run is paused."""


def unit_hash(key: str) -> float:
    """Deterministic number in [0, 1) for a key, so simulated step-up outcomes are repeatable."""
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


@dataclass
class Outcome:
    settled: bool  # money moved
    flagged: bool  # blocked or stepped up


def resolve(tx: Transaction, truth: GroundTruth, decision: Decision, fraud_step_up_pass: float,
            legit_step_up_abandon: float) -> Outcome:
    """What actually happens to a payment after the decision (simulated step-up outcomes)."""
    if decision.action == Action.ALLOW:
        return Outcome(settled=True, flagged=False)
    if decision.action == Action.BLOCK:
        return Outcome(settled=False, flagged=True)
    u = unit_hash(f"step-up:{tx.tx_id}")
    passes = u < fraud_step_up_pass if truth.is_fraud else u >= legit_step_up_abandon
    return Outcome(settled=passes, flagged=True)


def with_tx_id(decision: Decision, tx_id: str) -> Decision:
    votes = [v.model_copy(update={"tx_id": tx_id}) for v in decision.votes]
    return decision.model_copy(update={"tx_id": tx_id, "votes": votes})


class FraudEngine:
    def __init__(self, store: LogStore | None = None, pipeline: DefensePipeline | None = None, run_id: str | None = None,
                 seed: int = 42, allowlist: set[str] | None = None, scanner: PageScanner | None = None,
                 settlement: Settlement | None = None, live_settlement: Settlement | None = None,
                 live_per_round: int = 1, transport: str = "inprocess", world: World | None = None, n_legit: int = 300,
                 fraud_per_persona: int = 25, brain: Brain | None = None, attackers: list | None = None,
                 voters: list | None = None, round_budget_usd: float = ROUND_BUDGET_USD):
        if transport not in ("inprocess", "bureau"):
            raise ValueError("transport must be 'inprocess' or 'bureau'")
        self.store = store or MemoryStore()
        self.seed = seed
        self.run_id = run_id or f"dev-seed{seed}"
        self.checks_run_id = f"{self.run_id}-checks"
        self.world = world or World(seed=seed)
        self.pipeline = pipeline or DefensePipeline(identities=self.world.identities, voters=voters)
        self.allowlist = allowlist if allowlist is not None else team_allowlist()
        self.scanner = scanner or StubPageScanner()
        self.settlement = settlement or StubSettlement()  # every allowed round payment; must be a stub
        # The demo's live tail: at most `live_per_round` honest allowed payments per round also go
        # through real x402, at a token amount within the testnet cap (spec M13).
        self.live_settlement = live_settlement
        self.live_per_round = live_per_round
        self.transport = transport
        self.n_legit = n_legit
        self.fraud_per_persona = fraud_per_persona  # payments per outsider lineage per round
        self.brain = brain or Brain()
        self.attackers = attackers if attackers is not None else [
            OutsiderAttacker(persona, self.brain, self.world.products, self.world.identities, self.world.round_seconds,
                             role=("outsider_a", "outsider_b")[i % 2], mandate_for=self.world.fraud_mandate)
            for i, persona in enumerate(PERSONAS)]
        self.frozen = False
        self.paused: str | None = None
        self.round_budget_usd = round_budget_usd
        self.bureau = None
        self._lock = threading.RLock()
        self._round_lock = threading.Lock()
        self.learning = LearningLoop(self.pipeline, self.brain, self.store, self.run_id, FP_CAP, lock=self._lock)
        self.last_learning = None
        self._latest_check: dict[str, str] = {}  # caller tx_id -> stored id of its latest check
        self._round_decisions: dict[str, Decision] = {}  # this round's, until its batch reaches the database
        self.store.start_run(self.run_id, seed=seed, defense="full", config={"transport": transport, "milestone": "S3"})
        self.store.start_run(self.checks_run_id, seed=seed, defense="checks", config={"of_run": self.run_id})
        latest = self.store.get_metrics(self.run_id)
        self.round = latest.round if latest else 0  # resume when restarted on an existing run_id
        if self.round:
            self._resume_attackers()
            self._resume_rulebooks()
            controls = [r for r in self.store.get_rows("defender_actions", self.run_id) if r["action"] in ("pause", "resume")]
            if controls and controls[-1]["action"] == "pause":
                self.paused = controls[-1]["reason"]  # a pause survives a restart

    def _resume_attackers(self) -> None:
        """Pick each outsider lineage back up from its last logged record, so a restart mid-run
        doesn't send it back to its starting persona."""
        latest = {row["attacker_id"]: row["payload"] for row in self.store.get_rows("attackers", self.run_id)}
        for attacker in self.attackers:
            payload = latest.get(attacker.attacker_id)
            if payload is not None and hasattr(attacker, "restore"):
                attacker.restore(AttackerRecord.model_validate(payload))

    def _resume_rulebooks(self) -> None:
        """Pick the learned rulebooks back up: the latest deployed version of each lane."""
        from engine.dsl import Rulebook
        from engine.specialists import Specialist

        latest = {}
        for row in self.store.get_rows("rulebook_versions", self.run_id):
            if row["deployed"]:
                latest[row["lane"]] = row["rules"]
        for lane, specialist in list(self.pipeline.specialists.items()):
            if lane.value in latest:
                book = Rulebook(lane=lane, version=specialist.rulebook.version + 1, rules=latest[lane.value])
                self.pipeline.specialists[lane] = Specialist(lane, book, self.pipeline.catalog)

    def resume(self, requested_by: str) -> None:
        """Lift a pause from the spend stop rule (team only)."""
        self._require_team(requested_by)
        if self.paused:
            self._log_control(self.round, "resume", f"resumed by {requested_by}")
        self.paused = None

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> "FraudEngine":
        if self.transport == "bureau" and self.bureau is None:
            from engine.bureau import EngineBureau

            self.bureau = EngineBureau(self.pipeline)
            self.bureau.start()
        return self

    def stop(self) -> None:
        if self.bureau is not None:
            self.bureau.stop()
            self.bureau = None

    def _decide(self, tx: Transaction, commit: bool) -> tuple[Decision, FeatureVector]:
        if self.transport == "bureau":
            if self.bureau is None:
                raise RuntimeError("call start() before deciding with the bureau transport")
            return self.bureau.submit_with_features(tx, commit=commit)
        return self.pipeline.decide(tx, commit=commit)

    def _require_team(self, requested_by: str) -> None:
        if requested_by not in self.allowlist:
            raise NotAllowed(requested_by)

    def _stored_check_id(self, tx_id: str) -> str:
        """A fresh, engine-made id for an outside check, so repeats never collide."""
        base, n = f"{CHECK_PREFIX}{tx_id}", 1
        candidate = base
        while self.store.get_decision(candidate) is not None:
            n += 1
            candidate = f"{base}#{n}"
        return candidate

    # --- entry points (contracts.entrypoints.Engine) --------------------------------------

    def check_payment(self, tx: Transaction, mandate: Mandate | None = None) -> Decision:
        if mandate is not None:  # an explicit mandate wins, e.g. a what-if with a tighter cap
            tx = tx.model_copy(update={"mandate": mandate})
        with self._lock:
            clock = self.pipeline.features.clock
            if tx.ts < clock:  # an outside check happens "now" (chat sends ts=0)
                tx = tx.model_copy(update={"ts": clock})
            decision, features = self._decide(tx, commit=False)
            stored_id = self._stored_check_id(tx.tx_id)
            stored_tx = tx.model_copy(update={"tx_id": stored_id, "run_id": self.checks_run_id})
            log_bundle(self.store, stored_tx, features.model_copy(update={"tx_id": stored_id}),
                       with_tx_id(decision, stored_id))
            self._latest_check[tx.tx_id] = stored_id
        return decision

    def explain(self, tx_id: str) -> Decision:
        with self._lock:
            if tx_id in self._round_decisions:  # decided this round, not flushed yet
                return with_tx_id(self._round_decisions[tx_id], tx_id)
            stored_id = self._latest_check.get(tx_id)
            decision = self.store.get_decision(stored_id) if stored_id else None
            if decision is None:
                decision = self.store.get_decision(tx_id)  # a round payment, or a stored check id
            if decision is None and not tx_id.startswith(CHECK_PREFIX):
                decision = self.store.get_decision(f"{CHECK_PREFIX}{tx_id}")  # a check from before a restart
        if decision is None:
            raise KeyError(f"no decision logged for {tx_id!r}")
        return with_tx_id(decision, tx_id)

    def scan_page(self, url: str) -> PageScan:
        return self.scanner.scan(url)

    def get_metrics(self, run_id: str | None = None, round: int | None = None) -> RoundMetrics:
        run = run_id or self.run_id
        metrics = self.store.get_metrics(run, round)
        return metrics or RoundMetrics(run_id=run, seed=self.seed, round=round or 0, frozen=self.frozen)

    def run_round(self, requested_by: str) -> RoundMetrics:
        self._require_team(requested_by)
        with self._round_lock:
            if self.paused:
                raise LLMBudgetExceeded(self.paused)
            with self._lock:
                self.round += 1
                round_no = self.round
                frozen = self.frozen
            costs = self.pipeline.orchestrator.costs
            pass_rate = {a.attacker_id: getattr(getattr(a, "persona", None), "step_up_pass", costs.fraud_step_up_pass)
                         for a in self.attackers}
            spend_before = self.brain.spend()
            records: list[Record] = []
            self._live_sent = 0
            with self.store.batch():
                for wave, legit in enumerate(self._legit_by_wave(round_no)):
                    traffic = list(legit)
                    for attacker in self.attackers:
                        n = self.fraud_per_persona // WAVES + (wave < self.fraud_per_persona % WAVES)
                        traffic += [(tx, attacker.truth(tx)) for tx in attacker.generate(self.run_id, round_no, wave, n, self.seed)]
                    feedback: dict[str, list[AttackFeedback]] = defaultdict(list)
                    with self._lock:  # the defense is fixed within the wave (and the round)
                        for tx, truth in sorted(traffic, key=lambda pair: (pair[0].ts, pair[0].tx_id)):
                            record = self._process(tx, truth, pass_rate.get(tx.attacker_id, costs.fraud_step_up_pass))
                            records.append(record)
                            if truth.is_fraud:
                                feedback[tx.attacker_id].append(AttackFeedback(tx_id=tx.tx_id, outcome=record.decision.action,
                                                                               settled=record.settled))
                    for attacker in self.attackers:  # outcomes only: outsiders never learn who objected
                        attacker.observe(feedback[attacker.attacker_id])
                        self._log_wave(attacker, round_no)
                self.last_learning = None if frozen else self.learning.learn(round_no, records)
                self.store.log_rows("attackers", [self._attacker_row(a, round_no) for a in self.attackers])
                spend = self._log_spend(round_no, spend_before, self.brain.spend())
                legit = [r for r in records if not r.truth.is_fraud]
                learned = self.last_learning
                metrics = RoundMetrics(
                    run_id=self.run_id, seed=self.seed, round=round_no, defense="full",
                    loss_merchant_side_cents=sum(r.tx.amount_cents for r in records if r.truth.is_fraud and r.settled),
                    false_positive_rate=sum(r.decision.action != Action.ALLOW for r in legit) / max(1, len(legit)),
                    escalation_rate=0.0, frozen=frozen,
                    total_attacker_profit_cents=sum(getattr(a, "round_stats", {}).get("profit", 0) for a in self.attackers),
                    total_cost_cents=int(sum(realized_cost(r, costs) for r in records)),
                    uncovered_misses=learned.uncovered if learned else 0,
                    critic_agreement=learned.critic_agreement if learned else None,
                    llm_spend_usd=round(spend, 6),
                )
                self.store.log_round_metrics(metrics)
                if spend > 2 * self.round_budget_usd:
                    self.paused = (f"round {round_no} cost ${spend:.2f}, more than twice the ${self.round_budget_usd:.2f} "
                                   f"estimate; the run is paused until someone resumes it")
                    self._log_control(round_no, "pause", self.paused)
            with self._lock:
                self._round_decisions = {}
        return metrics

    def _log_control(self, round_no: int, action: str, reason: str) -> None:
        """Run-level decisions (the spend pause and its lifting) go in the orchestrator's action log."""
        self.store.log_rows("defender_actions", [{"run_id": self.run_id, "round": round_no, "actor": "orchestrator",
                                                  "action": action, "cost": 0, "granted": True, "reason": reason}])

    def _log_spend(self, round_no: int, before: dict, after: dict) -> float:
        """Log the round's model spend per role (spec 4.1) and return its total in dollars."""
        rows = []
        before_roles, after_roles = before.get("by_role", {}), after.get("by_role", {})
        for role, now in after_roles.items():
            was = before_roles.get(role, {})
            calls = now.get("calls", 0) - was.get("calls", 0)
            if calls:
                rows.append({"run_id": self.run_id, "round": round_no, "role": role, "model": self.brain.model_for(role),
                             "input_tokens": int(now.get("input_tokens", 0) - was.get("input_tokens", 0)),
                             "output_tokens": int(now.get("output_tokens", 0) - was.get("output_tokens", 0)),
                             "cost_usd": round(now.get("cost_usd", 0.0) - was.get("cost_usd", 0.0), 6)})
        total = float(after.get("cost_usd", 0.0)) - float(before.get("cost_usd", 0.0))
        unattributed = round(total - sum(r["cost_usd"] for r in rows), 6)
        if unattributed:  # the client reported spend it didn't break down by role
            rows.append({"run_id": self.run_id, "round": round_no, "role": "unattributed", "model": "unknown",
                         "input_tokens": 0, "output_tokens": 0, "cost_usd": unattributed})
        self.store.log_rows("llm_spend", rows)
        return total

    def _legit_by_wave(self, round_no: int) -> list[list[tuple[Transaction, GroundTruth]]]:
        """This round's legitimate traffic, split into WAVES equal time slices."""
        span = self.world.round_seconds
        start = (round_no - 1) * span
        waves: list[list[tuple[Transaction, GroundTruth]]] = [[] for _ in range(WAVES)]
        for tx, truth in self.world.legit_traffic(self.run_id, round_no, self.n_legit, start, span):
            wave = min(WAVES - 1, max(0, int((tx.ts - start) // (span / WAVES))))
            waves[wave].append((tx if tx.wave == wave else tx.model_copy(update={"wave": wave}), truth))
        return waves

    def _process(self, tx: Transaction, truth: GroundTruth, fraud_step_up_pass: float) -> Record:
        """Decide one round payment, log it, settle it if allowed, and update the daily spend."""
        costs = self.pipeline.orchestrator.costs
        decision, features = self._decide(tx, commit=True)
        log_bundle(self.store, tx, features, decision, truth)
        self._round_decisions[tx.tx_id] = decision
        outcome = resolve(tx, truth, decision, fraud_step_up_pass, costs.legit_step_up_abandon)
        if decision.action == Action.ALLOW:  # the gate: only allowed payments reach x402
            if self.live_settlement is not None and not truth.is_fraud and self._live_sent < self.live_per_round:
                self._live_sent += 1
                self.store.log_settlement(self._settle_live(tx, decision))
            else:
                self.store.log_settlement(self.settlement.settle(tx, decision))
        if outcome.settled:
            self.pipeline.features.record_spend(tx)
        return Record(tx, features, decision, truth, outcome.settled)

    def _log_wave(self, attacker, round_no: int) -> None:
        logs = getattr(attacker, "wave_logs", None)
        if not logs:
            return
        entry = logs[-1]
        self.store.log_rows("playbook_waves", [{
            "attacker_id": attacker.attacker_id, "run_id": self.run_id, "round": round_no, "wave": entry.wave,
            "playbook": entry.playbook.model_dump(mode="json"), "strategy_changed": entry.strategy_changed,
            "trigger": entry.trigger}])

    def _attacker_row(self, attacker, round_no: int) -> dict:
        record = attacker.record()
        stats = getattr(attacker, "round_stats", {})
        payload = {**record.model_dump(mode="json"), "extraction_cents": stats.get("extraction", 0),
                   "attempts": stats.get("attempts", 0), "caught": stats.get("caught", 0),
                   "rewrites": dict(getattr(attacker, "rewrites", {}))}
        return {"attacker_id": record.attacker_id, "run_id": self.run_id, "round": round_no,
                "source": record.source.value, "payload": payload}

    def _settle_live(self, tx: Transaction, decision: Decision):
        """One real testnet payment for the demo, at a token amount within the settlement's cap."""
        cap = getattr(self.live_settlement, "max_cents", 100)
        token = tx.model_copy(update={"amount_cents": max(1, min(tx.amount_cents, cap))})
        return self.live_settlement.settle(token, decision)

    def set_freeze(self, on: bool, requested_by: str) -> bool:
        """While frozen: no reflection, no weight updates, no retraining. Attackers keep adapting."""
        self._require_team(requested_by)
        with self._lock:
            self.frozen = on
            return self.frozen

    def latest_threat_intel(self, limit: int = 5) -> list[ThreatIntelItem]:
        return []  # M11 (bonus) fills this in

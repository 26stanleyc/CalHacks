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

S2 scope: decisions, logging, explain, metrics and a scripted round. Learning (S4), the
investigator (S5), the arena (S6) and mandates scoring (S7) plug in later.
"""

import hashlib
import threading
from dataclasses import dataclass

from contracts.api import team_allowlist
from contracts.entrypoints import NotAllowed
from contracts.interfaces import PageScanner
from contracts.schemas import (Action, Decision, FeatureVector, GroundTruth, Mandate, PageScan, RoundMetrics,
                               ThreatIntelItem, Transaction)
from contracts.stubs import StubPageScanner
from engine.pipeline import DefensePipeline
from engine.sim.world import World
from engine.store import LogStore, MemoryStore, log_bundle

CHECK_PREFIX = "check:"


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
                 transport: str = "inprocess", world: World | None = None, n_legit: int = 300,
                 fraud_per_persona: int = 25):
        if transport not in ("inprocess", "bureau"):
            raise ValueError("transport must be 'inprocess' or 'bureau'")
        self.store = store or MemoryStore()
        self.seed = seed
        self.run_id = run_id or f"dev-seed{seed}"
        self.checks_run_id = f"{self.run_id}-checks"
        self.world = world or World(seed=seed)
        self.pipeline = pipeline or DefensePipeline(identities=self.world.identities)
        self.allowlist = allowlist if allowlist is not None else team_allowlist()
        self.scanner = scanner or StubPageScanner()
        self.transport = transport
        self.n_legit = n_legit
        self.fraud_per_persona = fraud_per_persona
        self.frozen = False
        self.bureau = None
        self._lock = threading.RLock()
        self._latest_check: dict[str, str] = {}  # caller tx_id -> stored id of its latest check
        self.store.start_run(self.run_id, seed=seed, defense="full", config={"transport": transport, "milestone": "S2"})
        self.store.start_run(self.checks_run_id, seed=seed, defense="checks", config={"of_run": self.run_id})
        latest = self.store.get_metrics(self.run_id)
        self.round = latest.round if latest else 0  # resume when restarted on an existing run_id

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
            decision, features = self._decide(tx, commit=False)
            stored_id = self._stored_check_id(tx.tx_id)
            stored_tx = tx.model_copy(update={"tx_id": stored_id, "run_id": self.checks_run_id})
            log_bundle(self.store, stored_tx, features.model_copy(update={"tx_id": stored_id}),
                       with_tx_id(decision, stored_id))
            self._latest_check[tx.tx_id] = stored_id
        return decision

    def explain(self, tx_id: str) -> Decision:
        with self._lock:
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
        with self._lock:
            self.round += 1
            round_no = self.round
            traffic = self.world.round_traffic(self.run_id, round_no, self.n_legit, self.fraud_per_persona)
            costs = self.pipeline.orchestrator.costs
            loss = legit_n = legit_flagged = 0
            with self.store.batch():
                for tx, truth in traffic:
                    decision, features = self._decide(tx, commit=True)
                    log_bundle(self.store, tx, features, decision, truth)
                    outcome = resolve(tx, truth, decision, costs.fraud_step_up_pass, costs.legit_step_up_abandon)
                    if outcome.settled:
                        self.pipeline.features.record_spend(tx)
                    if truth.is_fraud and outcome.settled:
                        loss += tx.amount_cents
                    if not truth.is_fraud:
                        legit_n += 1
                        legit_flagged += outcome.flagged
                metrics = RoundMetrics(
                    run_id=self.run_id, seed=self.seed, round=round_no, defense="full",
                    loss_merchant_side_cents=loss, false_positive_rate=legit_flagged / max(1, legit_n),
                    escalation_rate=0.0, frozen=self.frozen,
                )
                self.store.log_round_metrics(metrics)
        return metrics

    def set_freeze(self, on: bool, requested_by: str) -> bool:
        self._require_team(requested_by)
        with self._lock:
            self.frozen = on
            return self.frozen

    def latest_threat_intel(self, limit: int = 5) -> list[ThreatIntelItem]:
        return []  # M11 (bonus) fills this in

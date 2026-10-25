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

Close calls go to the investigator (spec 3.4 point 4, M6) within an escalation budget: at most 10% of
a round's payments, and at most `escalation_cap` (100 in live and dev rounds, 40 in precompute).
Before that, with CRS escalation on, payments whose combined score falls in the uncertain band get
the CRS checks (engine/crs.py; mocked until there's sandbox access).

The Tag-Team Arena (spec 3.9, M7) adds spawned insiders and archive attackers to each round, keeps the
attack archive, re-scores it after learning, lets the specialists choose actions, and runs the
exploitability exam (engine/arena/arena.py). Mandate scoring (S7) plugs in later.
"""

import hashlib
import os
import threading
from collections import defaultdict
from dataclasses import dataclass

from contracts.api import team_allowlist
from contracts.entrypoints import NotAllowed
from contracts.interfaces import PageScanner, Settlement
from contracts.schemas import (Action, AttackerRecord, AttackFeedback, Decision, FeatureVector, GroundTruth, Lane, Mandate,
                               PageScan, RoundMetrics, Side,
                               ThreatIntelItem, Transaction)
from contracts.stubs import StubPageScanner, StubSettlement
from engine.arena.archive import Archive
from engine.arena.arena import Arena
from engine.arena.hijacker import HijackerAttacker
from engine.arena.merchant import MaliciousMerchantAttacker
from engine.arena.outsiders import PERSONAS, OutsiderAttacker
from engine.arena import sandbox as arena_sandbox
from engine.arena.playbooks import WAVES
from engine.brains import Brain, parallel
from engine.crs import CrsEscalation, crs_from_env
from engine.crs_enrollment import CrsEnrollment
from engine.trust_line import TrustLine, apply_trust_line
from engine.features import PageRisk
from engine.intel.corpus import CorpusSource
from engine.intel.watchlist import INTEL_PAYMENTS, Watchlist
from engine.investigator import Investigator
from engine.learning.labels import Record, realized_cost
from engine.learning.loop import LearningLoop
from engine.page_cache import PrescannedPages
from engine.pipeline import DefensePipeline
from engine.sim.pages import WorldPageScanner
from engine.sim.world import World
from engine.store import LogStore, MemoryStore, log_bundle

CHECK_PREFIX = "check:"
ROUND_BUDGET_USD = 0.60  # spec 4.1's per-round estimate; a round over twice this pauses the run
FP_CAP = 0.02
ESCALATION_SHARE = 0.10  # of a round's payments
ESCALATION_CAP = 100  # per round in live and dev rounds; precompute runs pass 40


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
    """`hijacker` and `merchants` switch the M9 and M10 lineages on; both default to on. A test
    written before a lineage existed passes it False, so its traffic mix keeps the meaning it had
    when its numbers were chosen."""

    def __init__(self, store: LogStore | None = None, pipeline: DefensePipeline | None = None, run_id: str | None = None,
                 seed: int = 42, allowlist: set[str] | None = None, scanner: PageScanner | None = None,
                 settlement: Settlement | None = None, live_settlement: Settlement | None = None,
                 live_per_round: int | None = 1, transport: str = "inprocess", world: World | None = None, n_legit: int = 300,
                 fraud_per_persona: int = 25, brain: Brain | None = None, attackers: list | None = None,
                 voters: list | None = None, round_budget_usd: float = ROUND_BUDGET_USD,
                 escalation_cap: int = ESCALATION_CAP, held_out: list | None = None, tuning_tries: int | None = None,
                 arena: bool = True, hijacker: bool = True, merchants: bool = True, drafts: bool = False,
                 threat_intel: bool = False, intel_watch: bool = True, threat_source=None, stacking: bool = True,
                 crs: CrsEscalation | None = None, crs_enrollment: bool | None = None, roster=None):
        if transport not in ("inprocess", "bureau"):
            raise ValueError("transport must be 'inprocess' or 'bureau'")
        self.store = store or MemoryStore()
        self.seed = seed
        self.run_id = run_id or f"dev-seed{seed}"
        self.checks_run_id = f"{self.run_id}-checks"
        self.world = world or World(seed=seed)
        self.pipeline = pipeline or DefensePipeline(identities=self.world.identities, voters=voters,
                                                    product_text=self.world.product_text)
        self.allowlist = allowlist if allowlist is not None else team_allowlist()
        # The world serves a product page for every product (M10), so the default scanner reads
        # those. It scores them with the same detector that scores a real scraped page.
        self.scanner = scanner or (WorldPageScanner(self.world.pages) if hasattr(self.world, "pages")
                                   else StubPageScanner())
        self.settlement = settlement or StubSettlement()  # every allowed round payment; must be a stub
        # The demo's live tail: at most `live_per_round` honest allowed payments per round also go
        # through real x402, at a token amount within the testnet cap (spec M13). None settles every
        # allowed payment live, fraud that got through included (ENGINE_LIVE_SETTLEMENT=all).
        self.live_settlement = live_settlement
        self.live_per_round = live_per_round
        self.transport = transport
        self.n_legit = n_legit
        self.fraud_per_persona = fraud_per_persona  # payments per outsider lineage per round
        self.brain = brain or Brain()
        self.attackers = attackers if attackers is not None else [
            OutsiderAttacker(persona, self.brain, self.world.products, self.world.identities, self.world.round_seconds,
                             role=("outsider_a", "outsider_b")[i % 2], mandate_for=self.world.fraud_mandate)
            for i, persona in enumerate(PERSONAS)] + ([  # the hijacker lineage joined in M9
            HijackerAttacker(self.brain, self.world.products, self.world.identities, self.world.round_seconds,
                             self.world.victims(), role="outsider_a")] if hijacker else []) + ([
            # the malicious merchant joined in M10: the only buyer-side lineage
            MaliciousMerchantAttacker(self.brain, self.world.products, self.world.identities,
                                      self.world.round_seconds, self.world.victims(), self.scanner,
                                      role="outsider_b")] if merchants and hasattr(self.world, "pages") else [])
        self.frozen = False
        self.paused: str | None = None
        self.round_budget_usd = round_budget_usd
        self.bureau = None
        self._lock = threading.RLock()
        self._round_lock = threading.Lock()
        self.learning = LearningLoop(self.pipeline, self.brain, self.store, self.run_id, FP_CAP, lock=self._lock,
                                     drafts=drafts, stacking=stacking)
        self.escalation_cap = escalation_cap
        self.page_url_for = getattr(self.world, "page_url_for", lambda tx: None)
        self.pages = PrescannedPages(self.scanner)  # payments never wait on a live page scan
        self.investigator = Investigator(self.brain, self.pipeline, self.pages, page_url_for=self.page_url_for)
        # Merchant-risk features read cached page scans only (spec 3.2): pages are scanned once per
        # round in _prime_pages, never on a payment's decision path.
        self.page_risk = PageRisk(self.scanner, self.page_url_for)
        self.pipeline.features.page_risk = self.page_risk
        self.escalation_seconds: list[float] = []  # every escalated case's decision time
        # CRS checks on uncertain payments: pass one in, or set CRS_ESCALATION (1 = mock, live = sandbox).
        # The bureau has files on everyone who exists now (attackers' later accounts aren't on file).
        self.crs = crs if crs is not None else crs_from_env(self.world.identities, set(self.world.identities))
        # Enrollment: the same client asks CRS who each person is, once, so the identity and graph
        # specialists and the trust line vote with the bureau's answer on every payment, not only on
        # the few the escalation path sees. Off whenever CRS is off.
        # Enrollment is on whenever CRS is, unless a caller turns it off to measure the escalation
        # path on its own: once the trust line narrows a made-up identity's cap, most of that
        # account's payments are hard-vetoed over cap and never reach an escalation at all.
        if crs_enrollment is None:
            flag = os.environ.get("CRS_ENROLLMENT", "").strip().lower()
            enroll = (self.crs is not None) if flag == "" else flag not in ("0", "off", "false", "no")
        else:
            enroll = crs_enrollment
        self.enrollment = CrsEnrollment(self.crs.client) if (self.crs and enroll) else None
        self.pipeline.features.crs = self.enrollment
        self.trust_lines: dict[str, TrustLine] = {}  # principal -> the limit CRS underwrote for it
        # The threat-intel watchlist (M11, bonus): needs the arena (its attackers join the archive).
        # intel_watch=False is the "intel off" ablation: the attackers still arrive, no watch rules.
        self.watchlist = Watchlist(self.brain, threat_source or CorpusSource(), self.world, self.run_id,
                                   intel_on=intel_watch, seed=seed) if threat_intel and arena else None
        self.learning.watchlist = self.watchlist
        self.archive = Archive()
        # roster(round_no, running) -> running: lets another run see or replace each round's attackers
        # before the waves (engine/live_vs_xgboost.py runs XGBoost against TagTeam's attackers).
        self.roster = roster
        # Without the arena (M3 alone: "until the archive exists, outsiders run on their own") no
        # attackers are spawned or replayed and there's no exam.
        self.arena = Arena(self.brain, self.archive, self.world, self.run_id, seed, self.pipeline.lanes,
                           held_out=held_out, tuning_tries=tuning_tries or arena_sandbox.TUNING_TRIES) if arena else None
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
            if self.watchlist:
                self.watchlist.restore(self.store.get_rows("defender_actions", self.run_id),
                                       [r for s in self.pipeline.specialists.values() for r in s.rulebook.rules], self.round)
            if self.crs:
                self.crs.restore(self.store.get_rows("defender_actions", self.run_id))
            if self.arena:
                self.arena.restore(self.store.get_rows("archive_snapshots", self.run_id),
                                   self.store.get_rows("attackers", self.run_id),
                                   self.store.get_rows("defender_actions", self.run_id))
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

    def _trust_lined(self, tx: Transaction) -> Transaction:
        """The payment, against the limits the agent was actually given rather than the ones its
        owner wrote down (`engine/trust_line.py`). CRS underwrites the mandate once, when the person
        enrolls; from then on the caps the hard veto enforces are the underwritten ones. With CRS
        off, or with no mandate, the payment is unchanged."""
        if self.enrollment is None or tx.mandate is None:
            return tx
        line = self.trust_lines.get(tx.principal_id)
        if line is None:
            profile = self.enrollment.profile(tx.principal_id)
            _, line = apply_trust_line(profile, tx.mandate)
            self.trust_lines[tx.principal_id] = line
        if (line.per_payment_cap_cents >= tx.mandate.per_payment_cap_cents
                and line.daily_cap_cents >= tx.mandate.daily_cap_cents):
            return tx  # a full line changes nothing
        return tx.model_copy(update={"mandate": tx.mandate.model_copy(
            update={"per_payment_cap_cents": min(line.per_payment_cap_cents, tx.mandate.per_payment_cap_cents),
                    "daily_cap_cents": min(line.daily_cap_cents, tx.mandate.daily_cap_cents)})})

    def _decide(self, tx: Transaction, commit: bool) -> tuple[Decision, FeatureVector]:
        tx = self._trust_lined(tx)
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
        return self.pages.scan_now(url)

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
            page_rows = self._prime_pages()  # malicious storefronts' pages, then every page's scan
            extras = self.arena.attackers_for_round(round_no, self.pipeline) if self.arena else []  # spawned and archive
            intel = [(a, INTEL_PAYMENTS) for a in self.watchlist.running(round_no)] if self.watchlist else []
            running = [(a, self.fraud_per_persona) for a in self.attackers] + intel + extras
            if self.roster:
                running = self.roster(round_no, running)
            pass_rate = {a.attacker_id: getattr(getattr(a, "persona", None), "step_up_pass",
                                                getattr(a, "step_up_pass", costs.fraud_step_up_pass)) for a, _ in running}
            lane_names = {lane.value for lane in self.pipeline.lanes}
            spend_before = self.brain.spend()
            records: list[Record] = []
            self._live_sent = 0
            expected = self.n_legit + sum(n for _, n in running)
            budget = min(self.escalation_cap, int(ESCALATION_SHARE * expected))
            escalated = crs_checked = 0
            with self.store.batch():
                for wave, legit in enumerate(self._legit_by_wave(round_no)):
                    traffic = list(legit)
                    for attacker, per_round in running:
                        n = per_round // WAVES + (wave < per_round % WAVES)
                        traffic += [(tx, attacker.truth(tx)) for tx in attacker.generate(self.run_id, round_no, wave, n, self.seed)]
                    insiders = {a.attacker_id for a, _ in extras if hasattr(a, "sandbox")}
                    feedback: dict[str, list[AttackFeedback]] = defaultdict(list)
                    wave_budget = (budget * (wave + 1)) // WAVES  # unspent escalations carry into later waves
                    for tx, truth in sorted(traffic, key=lambda pair: (pair[0].ts, pair[0].tx_id)):
                        with self._lock:  # the defense is fixed within the round
                            decision, features = self._decide(tx, commit=True)
                        code_action = decision.action
                        if self.crs and self.crs.is_watched(tx.principal_id) and not decision.vetoed_by:
                            decision = self.crs.watch(tx, decision)  # no new CRS call
                        elif self.crs and crs_checked < self.crs.max_per_round and self.crs.in_band(decision):
                            crs_checked += 1  # CRS calls run outside the lock, like the investigator
                            decision = self.crs.check(tx, features, decision)
                        if escalated < wave_budget and self._escalates(tx, decision, features):
                            escalated += 1  # the investigator runs outside the lock: checks can interleave
                            decision, seconds = self.investigator.investigate(tx, features, decision)
                            self.escalation_seconds.append(seconds)
                        with self._lock:
                            record = self._finish(tx, truth, features, decision,
                                                  pass_rate.get(tx.attacker_id, costs.fraud_step_up_pass), code_action)
                        records.append(record)
                        if truth.is_fraud:
                            objected = ([Lane(v.voter) for v in record.decision.votes
                                         if v.voter in lane_names and v.vote != Action.ALLOW]
                                        if tx.attacker_id in insiders else None)  # insiders learn who objected
                            feedback[tx.attacker_id].append(AttackFeedback(tx_id=tx.tx_id, outcome=record.decision.action,
                                                                           settled=record.settled, objected_by=objected))
                    # outsiders get outcomes only; each adapts on its own (a rewrite is a model call), all at once
                    parallel(lambda attacker: attacker.observe(feedback[attacker.attacker_id]), [a for a, _ in running])
                    for attacker, _ in running:
                        self._log_wave(attacker, round_no)
                self.store.log_rows("merchant_pages", page_rows)
                self.store.log_rows("shopper_sessions", self._session_rows(round_no, running))
                if self.crs and self.crs.new_watches:
                    self.store.log_rows("defender_actions", [{
                        "run_id": self.run_id, "round": round_no, "actor": "crs", "action": "watch", "cost": 0,
                        "granted": True, "reason": f"FraudFinder rated this person high risk ({w['risk_score']})",
                        "detail": w} for w in self.crs.new_watches])
                    self.crs.new_watches = []
                if self.arena:
                    self.arena.after_waves(round_no, records, self.attackers + [a for a, _ in intel])
                if not frozen:  # labeled cases for the investigator's similar_cases (a frozen defense keeps no labels)
                    for r in records:
                        self.investigator.remember(r.tx, r.features, r.decision, r.truth.is_fraud)
                self.learning.shelf_of = {a.attacker_id: getattr(a, "shelf", a.playbook.shelf).id() for a, _ in running}
                self.last_learning = None if frozen else self.learning.learn(round_no, records)
                if self.arena:
                    self.store.log_rows("defender_actions", self.arena.after_learning(round_no, self.pipeline, records, frozen))
                    self.store.log_rows("archive_events", self.archive.event_rows(self.run_id, round_no))
                    self.store.log_rows("archive_snapshots", self.archive.snapshot_rows(self.run_id, round_no))
                self.store.log_rows("attackers", [self._attacker_row(a, round_no) for a, _ in running]
                                    + self._exam_rows(round_no))
                spend = self._log_spend(round_no, spend_before, self.brain.spend())
                legit = [r for r in records if not r.truth.is_fraud and not r.truth.breaches_mandate]  # see CONTRACT_NOTES
                learned = self.last_learning
                losses = self._losses_by_side(records, running)
                sessions = self._session_rows(round_no, running)
                hijacked = [row for row in sessions if row["hijacked"]]
                metrics = RoundMetrics(
                    run_id=self.run_id, seed=self.seed, round=round_no, defense="full",
                    loss_merchant_side_cents=losses[Side.MERCHANT], loss_buyer_side_cents=losses[Side.BUYER],
                    hijack_rate=sum(self._got_through(row, records) for row in hijacked) / len(sessions) if sessions else 0.0,
                    false_positive_rate=sum(r.decision.action != Action.ALLOW for r in legit) / max(1, len(legit)),
                    escalation_rate=escalated / max(1, len(records)), frozen=frozen,
                    total_attacker_profit_cents=self.archive.total_profit(),
                    exploitability_cents=self.arena.exam.exploitability_cents if self.arena and self.arena.exam else 0,
                    shelves_defended=round(self.archive.defended_share(round_no), 4),
                    archive_coverage=round(self.archive.coverage(), 4),
                    total_cost_cents=int(sum(realized_cost(r, costs) for r in records)),
                    uncovered_misses=learned.uncovered if learned else 0,
                    critic_agreement=learned.critic_agreement if learned else None,
                    llm_spend_usd=round(spend, 6),
                    first_contact_catch_rate=self.watchlist.first_contact(round_no, records) if self.watchlist else None,
                )
                self.store.log_round_metrics(metrics)
                if spend > 2 * self.round_budget_usd:
                    self.paused = (f"round {round_no} cost ${spend:.2f}, more than twice the ${self.round_budget_usd:.2f} "
                                   f"estimate; the run is paused until someone resumes it")
                    self._log_control(round_no, "pause", self.paused)
            with self._lock:
                self._round_decisions = {}
        return metrics

    def _prime_pages(self) -> list[dict]:
        """Publish every malicious storefront's current pages into the world, then scan whatever is
        not already cached (spec 3.2, M10). Returns merchant_pages rows for the scans taken now.

        This is the whole reason merchant risk is affordable on the decision path: the page is
        fetched and scored once here, and every payment at that store then reads a number.
        """
        store = getattr(self.world, "pages", None)
        for attacker in self.attackers:
            pages = getattr(attacker, "pages", None)
            if pages is None or store is None:
                continue
            published = pages()
            for url, (html, merchant_id) in published.items():
                store.put(url, html, merchant_id)
            # A rewritten playbook hides different text, so the old scan is stale.
            self.page_risk.forget(published)
            attacker.forget_pages()
        urls = self.world.page_urls() if hasattr(self.world, "page_urls") else []
        fresh = self.page_risk.prime(urls)
        self.pages.keep(fresh)  # the investigator reads the same scans
        return [{"url": scan.url, "merchant_id": scan.merchant_id, "payload": scan.model_dump(mode="json")}
                for scan in fresh]

    @staticmethod
    def _losses_by_side(records: list, running: list) -> dict:
        """Settled fraud, split by which side of the market lost the money (M10).

        The side comes from the attacker's own playbook shelf, which is what bench/metrics.py reads,
        so the engine's live number and Ryan's recomputed one agree. Merchant-side is every other
        attacker, including ones that ran in earlier rounds, so the two fields always add up to the
        round's total settled fraud.
        """
        sides = {a.attacker_id: a.playbook.shelf.side for a, _ in running if hasattr(a, "playbook")}
        out = {Side.MERCHANT: 0, Side.BUYER: 0}
        for r in records:
            if r.truth.is_fraud and r.settled:
                out[sides.get(r.tx.attacker_id, Side.MERCHANT)] += r.tx.amount_cents
        return out

    @staticmethod
    def _got_through(session: dict, records: list) -> bool:
        """Whether a hijacked session's payment actually settled."""
        tx_id = session["decision"]["tx_id"]
        return any(r.tx.tx_id == tx_id and r.settled for r in records)

    def _session_rows(self, round_no: int, running: list) -> list[dict]:
        """One row per page-reading shopper session this round (M10): which pages the agent read,
        what it bought, and whether the page talked it into something its owner never authorized."""
        rows = []
        for attacker, _ in running:
            for session in getattr(attacker, "sessions", []):
                rows.append({"run_id": self.run_id, "round": round_no, "agent_id": session.agent_id,
                             "pages_read": list(session.pages_read),
                             "decision": {"action": "buy", "tx_id": session.tx_id, "product": session.product,
                                          "breaches_mandate": session.breaches,
                                          "injection_score": round(session.injection_score, 4)},
                             "hijacked": session.hijacked})
        return rows

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

    def _escalates(self, tx: Transaction, decision: Decision, features: FeatureVector) -> bool:
        """Close calls go to the investigator (spec 3.4 point 4): the best and second-best actions cost
        about the same, or a specialist wanted a block the combined decision didn't give, unless the
        pattern is a familiar one the code already handles the way past cases went. Hard vetoes never
        escalate."""
        if decision.vetoed_by or decision.crs is not None and (decision.crs.decision != Action.STEP_UP or decision.crs.watch):
            return False  # CRS already allowed or blocked it, or the person is on the CRS watch list (never allowed)
        split = decision.action != Action.BLOCK and any(v.vote == Action.BLOCK and decision.weights.get(v.voter, 1.0) > 0
                                                        for v in decision.votes)  # a voter sitting out doesn't count
        if not (split or self.pipeline.orchestrator.is_close_call(decision.weighted_score, tx.amount_cents)):
            return False
        return not self.investigator.memory.familiar(features, decision.action != Action.ALLOW)

    def _process(self, tx: Transaction, truth: GroundTruth, fraud_step_up_pass: float) -> Record:
        """Decide one payment without escalation (used where no investigator is wanted)."""
        decision, features = self._decide(tx, commit=True)
        return self._finish(tx, truth, features, decision, fraud_step_up_pass, decision.action)

    def _finish(self, tx: Transaction, truth: GroundTruth, features: FeatureVector, decision: Decision,
                fraud_step_up_pass: float, code_action: Action) -> Record:
        """Log one round payment, settle it if allowed, and update the daily spend."""
        costs = self.pipeline.orchestrator.costs
        log_bundle(self.store, tx, features, decision, truth)
        if decision.escalated and decision.investigator is not None:
            report = decision.investigator
            self.store.log_rows("escalations", [{
                "tx_id": tx.tx_id, "tool_calls": [c.model_dump(mode="json") for c in report.tool_calls],
                "decision": report.decision.value, "reasons": report.reasons, "timed_out": report.timed_out}])
        self._round_decisions[tx.tx_id] = decision
        outcome = resolve(tx, truth, decision, fraud_step_up_pass, costs.legit_step_up_abandon)
        if decision.action == Action.ALLOW:  # the gate: only allowed payments reach x402
            if self.live_settlement is not None and (self.live_per_round is None or
                                                     (not truth.is_fraud and self._live_sent < self.live_per_round)):
                self._live_sent += 1
                self.store.log_settlement(self._settle_live(tx, decision))
            else:
                self.store.log_settlement(self.settlement.settle(tx, decision))
        if outcome.settled:
            self.pipeline.features.record_spend(tx)
        return Record(tx, features, decision, truth, outcome.settled, code_action)

    def _log_wave(self, attacker, round_no: int) -> None:
        logs = getattr(attacker, "wave_logs", None)
        if not logs:
            return
        entry = logs[-1]
        self.store.log_rows("playbook_waves", [{
            "attacker_id": attacker.attacker_id, "run_id": self.run_id, "round": round_no, "wave": entry.wave,
            "playbook": entry.playbook.model_dump(mode="json"), "strategy_changed": entry.strategy_changed,
            "trigger": entry.trigger}])

    def _exam_rows(self, round_no: int) -> list[dict]:
        """Held-out attackers' exam results (they never run in round traffic or enter the archive)."""
        exam = self.arena.exam if self.arena else None
        if exam is None:
            return []
        rows = []
        for record in self.arena.held_out_records:
            rows.append({"attacker_id": record.attacker_id, "run_id": self.run_id, "round": round_no, "source": "held_out",
                         "payload": {**record.model_dump(mode="json"), "exam_extraction_cents": exam.held_out.get(record.attacker_id, 0)}})
        for attacker in self.arena.held_out_attackers:
            record = attacker.record()
            rows.append({"attacker_id": record.attacker_id, "run_id": self.run_id, "round": round_no, "source": "held_out",
                         "payload": {**record.model_dump(mode="json"), "exam_extraction_cents": exam.held_out.get(record.attacker_id, 0)}})
        return rows

    def _attacker_row(self, attacker, round_no: int) -> dict:
        record = attacker.record()
        stats = getattr(attacker, "round_stats", {})
        payload = {**record.model_dump(mode="json"), "extraction_cents": stats.get("extraction", 0),
                   "attempts": stats.get("attempts", 0), "caught": stats.get("caught", 0),
                   "rewrites": dict(getattr(attacker, "rewrites", {})), "objections": dict(getattr(attacker, "objections", {}))}
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
        if self.watchlist is None:
            return []
        with self._lock:
            return self.watchlist.latest(limit)

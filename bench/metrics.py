"""Round metrics from the log tables (R5, spec 5.1).

Everything works on `Tables`: table name -> list of row dicts, with the same column names as
contracts/db.sql and JSONB columns as plain dicts. `load_tables` reads them from Postgres for one
run; data/seed.py builds them in memory, so the same code is tested offline on seed data.

Definitions (where the spec leaves room, the choice is written here):
- Main traffic is waves 0-2; wave EXAM_WAVE is frozen exam traffic (proposed marker, see
  CONTRACT_NOTES.md). Chat-sourced (asi_one) payments are never counted.
- A fraud payment is a loss when it settled (settlements row), or, without a settlements row,
  when it was allowed. Its side comes from its attacker's shelf (merchant if unknown).
- False-positive rate: legit payments that were blocked or stepped up, over all legit payments.
- Recall at 1% FPR and PR-AUC rank payments by the decision's weighted_score.
- Exploitability: the most any single attacker extracted (allowed fraud cents) in the exam wave.
- Shelves defended: filled shelves whose raw catch rate (caught / attempts) is at least 80%.
- First-contact catch rate: threat-intel attackers whose first payment of the run is in this
  round, and the share of those first payments that weren't allowed.
- Uncovered misses: the critic's miss assignments that fit no lane (lane missing or "uncovered").
"""

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel
from sklearn.metrics import average_precision_score, roc_curve

from bench import config
from contracts.schemas import (
    Action,
    AttackerRecord,
    AttackerSource,
    Decision,
    RoundMetrics,
    Side,
    TrafficSource,
    Transaction,
)

Tables = dict[str, list[dict]]

RUN_TABLES = ("runs", "transactions", "attackers", "archive_snapshots", "critic_checks", "shopper_sessions",
              "round_metrics", "llm_spend")
TX_TABLES = ("ground_truth", "decisions", "settlements")


class BenchMetrics(BaseModel):
    """RoundMetrics plus the benchmark-only numbers that RoundMetrics has no field for yet."""

    metrics: RoundMetrics
    recall_at_target_fpr: float | None = None
    pr_auc: float | None = None
    total_cost_cents: int = 0
    n_transactions: int = 0
    n_fraud: int = 0


@dataclass
class _Labeled:
    tx: Transaction
    is_fraud: bool
    decision: Decision


def _rows(tables: Tables, name: str, run_id: str, round: int | None = None) -> list[dict]:
    return [r for r in tables.get(name, []) if r.get("run_id") == run_id and (round is None or r.get("round") == round)]


def attacker_records(tables: Tables, run_id: str, up_to_round: int) -> dict[str, AttackerRecord]:
    """Latest logged record per attacker at or before `up_to_round`."""
    latest: dict[str, tuple[int, int, dict]] = {}
    for i, r in enumerate(_rows(tables, "attackers", run_id)):
        if r["round"] <= up_to_round:
            key = (r["round"], r.get("id") or i)
            if r["attacker_id"] not in latest or key > latest[r["attacker_id"]][:2]:
                latest[r["attacker_id"]] = (*key, r["payload"])
    return {aid: AttackerRecord.model_validate(p) for aid, (_, _, p) in latest.items()}


def cost_cents(is_fraud: bool, action: Action, amount: int) -> float:
    return config.COSTS.expected_costs(1.0 if is_fraud else 0.0, amount)[action]


def recall_at_fpr(labels: list[bool], scores: list[float], target_fpr: float = config.TARGET_FPR) -> float | None:
    if not any(labels) or all(labels):
        return None
    fpr, tpr, _ = roc_curve(labels, scores)
    return float(max(t for f, t in zip(fpr, tpr) if f <= target_fpr + 1e-12))


def pr_auc(labels: list[bool], scores: list[float]) -> float | None:
    if not any(labels) or all(labels):
        return None
    return float(average_precision_score(labels, scores))


def compute_round_metrics(tables: Tables, run_id: str, round: int, frozen: bool | None = None) -> BenchMetrics:
    run = next(r for r in tables["runs"] if r["run_id"] == run_id)
    txs = [Transaction.model_validate(r["payload"]) for r in _rows(tables, "transactions", run_id, round)]
    tx_ids = {t.tx_id for t in txs}
    truth = {r["tx_id"]: r for r in tables.get("ground_truth", []) if r["tx_id"] in tx_ids}
    decisions = {r["tx_id"]: Decision.model_validate(r["payload"]) for r in tables.get("decisions", [])
                 if r["tx_id"] in tx_ids}
    settled = {r["tx_id"]: r["settled"] for r in tables.get("settlements", []) if r["tx_id"] in tx_ids}
    attackers = attacker_records(tables, run_id, round)

    def side_of(tx: Transaction) -> Side:
        rec = attackers.get(tx.attacker_id or "")
        return rec.playbook.shelf.side if rec else Side.MERCHANT

    def got_through(tx: Transaction, d: Decision) -> bool:
        return settled[tx.tx_id] if tx.tx_id in settled else d.action == Action.ALLOW

    scored = [t for t in txs if t.source != TrafficSource.ASI_ONE and t.tx_id in decisions]
    main = [t for t in scored if t.wave in config.MAIN_WAVES]
    exam = [t for t in scored if t.wave == config.EXAM_WAVE]
    labeled = [_Labeled(t, bool(truth[t.tx_id]["is_fraud"]), decisions[t.tx_id]) for t in main if t.tx_id in truth]

    loss = {Side.MERCHANT: 0, Side.BUYER: 0}
    for x in labeled:
        if x.is_fraud and got_through(x.tx, x.decision):
            loss[side_of(x.tx)] += x.tx.amount_cents
    legit = [x for x in labeled if not x.is_fraud]
    fp = sum(x.decision.action != Action.ALLOW for x in legit)

    extraction: dict[str, int] = {}
    for t in exam:
        if t.tx_id in truth and truth[t.tx_id]["is_fraud"] and got_through(t, decisions[t.tx_id]):
            extraction[t.attacker_id or "?"] = extraction.get(t.attacker_id or "?", 0) + t.amount_cents

    snapshots = [r["payload"] for r in _rows(tables, "archive_snapshots", run_id, round)]
    filled = [s for s in snapshots if s.get("attacker_ids")]
    defended = [s for s in filled
                if s.get("attempts", 0) > 0 and s["caught"] / s["attempts"] >= config.SHELF_DEFENDED_CATCH_RATE]
    on_shelves = {aid for s in filled for aid in s["attacker_ids"]}

    checks = _rows(tables, "critic_checks", run_id, round)
    uncovered = sum(c["kind"] == "miss_assignment" and (c["critic"] or {}).get("lane") in (None, "uncovered")
                    for c in checks)
    sessions = _rows(tables, "shopper_sessions", run_id, round)

    def hijack_succeeded(s: dict) -> bool:
        tx_id = (s.get("decision") or {}).get("tx_id")
        if not s["hijacked"]:
            return False
        if tx_id in decisions:
            return got_through(next(t for t in txs if t.tx_id == tx_id), decisions[tx_id])
        return bool((s.get("decision") or {}).get("settled", True))

    if frozen is None:
        logged = next((r for r in _rows(tables, "round_metrics", run_id, round)), None)
        frozen = bool(logged and logged["payload"].get("frozen"))

    metrics = RoundMetrics(
        run_id=run_id, seed=run["seed"], round=round, defense=run.get("defense", "full"),
        loss_merchant_side_cents=loss[Side.MERCHANT], loss_buyer_side_cents=loss[Side.BUYER],
        false_positive_rate=fp / len(legit) if legit else 0.0,
        escalation_rate=sum(decisions[t.tx_id].escalated for t in main) / len(main) if main else 0.0,
        uncovered_misses=uncovered,
        exploitability_cents=max(extraction.values(), default=0),
        hijack_rate=sum(map(hijack_succeeded, sessions)) / len(sessions) if sessions else 0.0,
        shelves_defended=len(defended) / len(filled) if filled else 0.0,
        archive_coverage=len(filled) / config.N_SHELVES,
        total_attacker_profit_cents=sum(attackers[a].profit_cents for a in on_shelves if a in attackers),
        first_contact_catch_rate=_first_contact(tables, run_id, round, attackers, decisions),
        critic_agreement=sum(bool(c["agree"]) for c in checks) / len(checks) if checks else None,
        llm_spend_usd=round_half(sum(float(r["cost_usd"]) for r in _rows(tables, "llm_spend", run_id, round))),
        frozen=frozen,
    )
    labels = [x.is_fraud for x in labeled]
    scores = [x.decision.weighted_score for x in labeled]
    return BenchMetrics(
        metrics=metrics, recall_at_target_fpr=recall_at_fpr(labels, scores), pr_auc=pr_auc(labels, scores),
        total_cost_cents=int(round_half(sum(cost_cents(x.is_fraud, x.decision.action, x.tx.amount_cents)
                                             for x in labeled))),
        n_transactions=len(labeled), n_fraud=sum(labels),
    )


def _first_contact(tables: Tables, run_id: str, round: int, attackers: dict[str, AttackerRecord],
                   decisions: dict[str, Decision]) -> float | None:
    intel = {a for a, rec in attackers.items() if rec.source == AttackerSource.THREAT_INTEL}
    first: dict[str, Transaction] = {}
    for r in _rows(tables, "transactions", run_id):
        if r["attacker_id"] in intel and r["wave"] in config.MAIN_WAVES:
            t = Transaction.model_validate(r["payload"])
            if t.attacker_id not in first or (t.ts, t.tx_id) < (first[t.attacker_id].ts, first[t.attacker_id].tx_id):
                first[t.attacker_id] = t
    new = [t for t in first.values() if t.round == round and t.tx_id in decisions]
    if not new:
        return None
    return sum(decisions[t.tx_id].action != Action.ALLOW for t in new) / len(new)


def round_half(x: float, digits: int = 5) -> float:
    return float(round(Decimal(str(x)), digits))


def compute_run_metrics(tables: Tables, run_id: str, frozen_rounds: set[int] | None = None) -> list[BenchMetrics]:
    rounds = sorted({r["round"] for r in _rows(tables, "transactions", run_id)})
    return [compute_round_metrics(tables, run_id, r, None if frozen_rounds is None else r in frozen_rounds)
            for r in rounds]


def load_tables(conn, run_id: str) -> Tables:
    """Read everything the metrics need for one run from Postgres."""
    tables: Tables = {}
    for name in RUN_TABLES:
        cur = conn.execute(f"select * from {name} where run_id = %s", (run_id,))  # names are constants above
        cols = [c.name for c in cur.description]
        tables[name] = [dict(zip(cols, row)) for row in cur.fetchall()]
    for name in TX_TABLES:
        cur = conn.execute(f"select t.* from {name} t join transactions x using (tx_id) where x.run_id = %s",
                           (run_id,))
        cols = [c.name for c in cur.description]
        tables[name] = [dict(zip(cols, row)) for row in cur.fetchall()]
    return tables


def metrics_from_db(conn, run_id: str, round: int) -> BenchMetrics:
    return compute_round_metrics(load_tables(conn, run_id), run_id, round)

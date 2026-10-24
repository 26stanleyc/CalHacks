import pytest

from bench import config
from bench.metrics import compute_round_metrics, compute_run_metrics, cost_cents, pr_auc, recall_at_fpr
from contracts.schemas import (
    Action,
    AmountBand,
    AttackerRecord,
    AttackerSource,
    AttackStyle,
    Decision,
    Playbook,
    ShelfKey,
    ShelfState,
    Side,
    TrafficSource,
    Transaction,
)
from data.seed import FROZEN_ROUNDS, ROUNDS, RUN_ID, build_seed

pytestmark = pytest.mark.m12


def _tx(i: int, fraud: bool, amount: int, action: Action, score: float, wave: int = 0, attacker: str | None = None,
        escalated: bool = False, source: TrafficSource | None = None):
    tx = Transaction(tx_id=f"t{i}", run_id="r", round=1, wave=wave, ts=float(i), agent_id="a", principal_id="p",
                     merchant_id="m", amount_cents=amount,
                     source=source or (TrafficSource.ATTACKER if fraud else TrafficSource.LEGIT), attacker_id=attacker)
    d = Decision(tx_id=tx.tx_id, action=action, weighted_score=score, votes=[], escalated=escalated)
    return tx, d


def _tables(rows, attackers=(), snapshots=(), settlements=None):
    t = {"runs": [{"run_id": "r", "seed": 7, "defense": "static_rules"}], "transactions": [], "ground_truth": [],
         "decisions": [], "settlements": settlements or [], "attackers": [], "archive_snapshots": [],
         "critic_checks": [], "shopper_sessions": [], "llm_spend": [], "round_metrics": []}
    for (tx, d), fraud in rows:
        t["transactions"].append({"tx_id": tx.tx_id, "run_id": "r", "round": 1, "wave": tx.wave,
                                  "source": tx.source.value, "attacker_id": tx.attacker_id,
                                  "payload": tx.model_dump(mode="json")})
        t["ground_truth"].append({"tx_id": tx.tx_id, "is_fraud": fraud, "attacker_id": tx.attacker_id})
        t["decisions"].append({"tx_id": tx.tx_id, "action": d.action.value, "payload": d.model_dump(mode="json")})
    for rec in attackers:
        t["attackers"].append({"attacker_id": rec.attacker_id, "run_id": "r", "round": 1, "source": rec.source.value,
                               "payload": rec.model_dump(mode="json")})
    for s in snapshots:
        t["archive_snapshots"].append({"run_id": "r", "round": 1, "shelf_id": s.key.id(),
                                       "payload": s.model_dump(mode="json")})
    return t


def _attacker(aid: str, side: Side, profit: int, source=AttackerSource.SPAWNED) -> AttackerRecord:
    key = ShelfKey(style=AttackStyle.BURST, amount=AmountBand.UNDER_10, side=side)
    return AttackerRecord(attacker_id=aid, name=aid, source=source, profit_cents=profit,
                          playbook=Playbook(playbook_id=f"{aid}-pb", attacker_id=aid, strategy="s", shelf=key))


def test_hand_computed_round():
    rows = [
        (_tx(1, False, 1_000, Action.ALLOW, 0.1), False),
        (_tx(2, False, 2_000, Action.BLOCK, 0.9), False),  # false positive
        (_tx(3, False, 1_000, Action.STEP_UP, 0.6, escalated=True), False),  # false positive, escalated
        (_tx(4, False, 1_000, Action.ALLOW, 0.2), False),
        (_tx(5, True, 5_000, Action.ALLOW, 0.3, attacker="am"), True),  # merchant-side loss
        (_tx(6, True, 700, Action.ALLOW, 0.4, attacker="ab"), True),  # buyer-side loss
        (_tx(7, True, 9_000, Action.BLOCK, 0.95, attacker="am"), True),
        (_tx(8, True, 3_000, Action.ALLOW, 0.2, wave=config.EXAM_WAVE, attacker="am"), True),  # exam
        (_tx(9, True, 4_000, Action.ALLOW, 0.2, wave=config.EXAM_WAVE, attacker="ab"), True),  # exam
        (_tx(10, True, 9_999, Action.ALLOW, 0.1, source=TrafficSource.ASI_ONE), True),  # chat: never counted
    ]
    shelf = ShelfKey(style=AttackStyle.BURST, amount=AmountBand.UNDER_10, side=Side.MERCHANT)
    snapshots = [ShelfState(key=shelf, attacker_ids=["am"], attempts=10, caught=9),
                 ShelfState(key=shelf.model_copy(update={"side": Side.BUYER}), attacker_ids=["ab"], attempts=10,
                            caught=5),
                 ShelfState(key=shelf.model_copy(update={"amount": AmountBand.OVER_100}))]
    tables = _tables(rows, [_attacker("am", Side.MERCHANT, 1_200), _attacker("ab", Side.BUYER, 300)], snapshots)
    bm = compute_round_metrics(tables, "r", 1)
    m = bm.metrics
    assert (m.seed, m.defense) == (7, "static_rules")
    assert m.loss_merchant_side_cents == 5_000 and m.loss_buyer_side_cents == 700
    assert m.false_positive_rate == pytest.approx(2 / 4)
    assert m.escalation_rate == pytest.approx(1 / 7)
    assert m.exploitability_cents == 4_000
    assert m.archive_coverage == pytest.approx(2 / 24) and m.shelves_defended == pytest.approx(1 / 2)
    assert m.total_attacker_profit_cents == 1_500
    assert bm.n_transactions == 7 and bm.n_fraud == 3
    expected_cost = (cost_cents(False, Action.BLOCK, 2_000) + cost_cents(False, Action.STEP_UP, 1_000)
                     + 5_000 + 700)
    assert bm.total_cost_cents == int(expected_cost)


def test_settlement_rows_decide_what_got_through():
    rows = [(_tx(1, True, 5_000, Action.STEP_UP, 0.6, attacker="am"), True),
            (_tx(2, False, 100, Action.ALLOW, 0.1), False)]
    settled = [{"tx_id": "t1", "settled": True}]
    tables = _tables(rows, [_attacker("am", Side.MERCHANT, 0)], settlements=settled)
    assert compute_round_metrics(tables, "r", 1).metrics.loss_merchant_side_cents == 5_000


def test_recall_at_fixed_fpr_and_pr_auc():
    labels = [False] * 100 + [True] * 10
    scores = [i / 1000 for i in range(100)] + [0.5] * 8 + [0.05, 0.01]
    assert recall_at_fpr(labels, scores, 0.01) == pytest.approx(0.8)
    assert 0.7 < pr_auc(labels, scores) <= 1.0
    assert recall_at_fpr([True, True], [0.1, 0.2]) is None and pr_auc([False], [0.1]) is None


def test_cost_matrix():
    assert cost_cents(True, Action.ALLOW, 1_000) == 1_000
    assert cost_cents(True, Action.BLOCK, 1_000) == 0
    assert cost_cents(True, Action.STEP_UP, 1_000) == pytest.approx(1_000 * config.FRAUD_STEP_UP_PASS_RATE)
    assert cost_cents(False, Action.ALLOW, 1_000) == 0
    assert cost_cents(False, Action.BLOCK, 1_000) > cost_cents(False, Action.STEP_UP, 1_000) > 0


def test_every_metric_on_the_seed_run():
    tables = build_seed()
    run = compute_run_metrics(tables, RUN_ID)
    assert [b.metrics.round for b in run] == list(range(1, ROUNDS + 1))
    for b in run:
        m = b.metrics
        assert b.recall_at_target_fpr is not None and b.pr_auc is not None and b.total_cost_cents > 0
        assert 0 <= m.false_positive_rate <= 0.05 and 0.04 <= m.escalation_rate <= 0.12
        assert m.loss_merchant_side_cents > 0 and m.loss_buyer_side_cents > 0
        assert 0 < m.archive_coverage <= 1 and 0 <= m.shelves_defended <= 1
        assert m.llm_spend_usd > 0 and m.critic_agreement is not None
        assert m.frozen == (m.round in FROZEN_ROUNDS)
        assert m.round == 1 or m.exploitability_cents > 0
    first_contact = {b.metrics.round: b.metrics.first_contact_catch_rate for b in run
                     if b.metrics.first_contact_catch_rate is not None}
    assert set(first_contact) == {3, 8}  # the two threat-intel reports
    assert run[-1].metrics.uncovered_misses < run[0].metrics.uncovered_misses

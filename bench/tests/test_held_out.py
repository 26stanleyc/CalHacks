import pytest

from bench.held_out import HELD_OUT_IDS, held_out_attackers
from contracts import config as llm_config
from contracts.conformance import check_attacker
from contracts.schemas import AttackerSource, AttackStyle, Side

pytestmark = pytest.mark.m07

OUTSIDER_PATTERNS = {"card tester", "structurer", "account farmer", "prompt-injection", "hijacker",
                     "malicious merchant"}


@pytest.mark.parametrize("attacker", held_out_attackers(), ids=lambda a: a.attacker_id)
def test_passes_the_attacker_contract(attacker):
    check_attacker(attacker)


def test_six_hand_written_held_out_attackers():
    attackers = held_out_attackers()
    assert len(attackers) == 6 and len(HELD_OUT_IDS) == 6
    for a in attackers:
        rec = a.record()
        assert rec.source == AttackerSource.HELD_OUT and rec.held_out
        assert rec.model is None, "hand-written: no LLM, and never the reserved model"
        assert rec.model != llm_config.MODEL_ROLES["held_out"]


def test_one_merchant_side_attacker_per_style_plus_two_buyer_side():
    shelves = [a.record().playbook.shelf for a in held_out_attackers()]
    merchant = [s.style for s in shelves if s.side == Side.MERCHANT]
    assert sorted(merchant) == sorted(AttackStyle)
    assert sum(s.side == Side.BUYER for s in shelves) == 2
    assert len({s.id() for s in shelves}) == 6


def test_patterns_differ_from_the_outsider_personas():
    for a in held_out_attackers():
        rec = a.record()
        assert rec.lineage is None
        assert not any(p in rec.name.lower() for p in OUTSIDER_PATTERNS), rec.name


@pytest.mark.parametrize("attacker", held_out_attackers(), ids=lambda a: a.attacker_id)
def test_deterministic_per_seed_and_inside_bounds(attacker):
    a = attacker.generate("exam", 3, 1, 12, 7)
    b = attacker.generate("exam", 3, 1, 12, 7)
    c = attacker.generate("exam", 3, 1, 12, 8)
    assert [t.model_dump() for t in a] == [t.model_dump() for t in b]
    assert [t.model_dump() for t in a] != [t.model_dump() for t in c]
    rec = attacker.record()
    for name, value in rec.playbook.params.items():
        lo, hi = rec.playbook.param_bounds[name]
        assert lo <= value <= hi
    lo, hi = rec.playbook.param_bounds.get("amount_cents", (0, float("inf")))
    for tx in a:
        assert tx.amount_cents > 0 and tx.mandate is not None
        assert 2 * 86_400 <= tx.ts < 3 * 86_400, "stays inside its round"
        if "amount_cents" in rec.playbook.params:
            assert lo <= tx.amount_cents <= hi
        assert attacker.truth(tx).is_fraud


def test_buyer_side_attackers_charge_real_shoppers_from_their_own_store():
    for a in held_out_attackers():
        if a.record().playbook.shelf.side == Side.BUYER:
            txs = a.generate("exam", 1, 0, 6, 42)
            assert {t.merchant_id for t in txs} == {a.merchant_id}
            assert all(t.mandate.principal_id == t.principal_id for t in txs)

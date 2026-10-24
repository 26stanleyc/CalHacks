import pytest

from bench import config
from bench.baselines import StaticRulesDefense, XGBoostDefense, should_retrain
from bench.held_out import held_out_attackers
from contracts.conformance import check_defense, sample_tx
from contracts.schemas import Action, FeatureVector, GroundTruth, Mandate, TrafficSource
from data.features import build_features
from data.shoppers import Population, generate_legit_traffic

pytestmark = pytest.mark.m12


@pytest.mark.parametrize("defense", [XGBoostDefense(), StaticRulesDefense()], ids=lambda d: d.name)
def test_passes_the_defense_contract(defense):
    check_defense(defense)


def test_names_match_the_benchmark_rows():
    assert XGBoostDefense().name == "xgboost" and StaticRulesDefense().name == "static_rules"


def test_retrain_schedule_comes_from_config():
    expected = [r for r in range(1, 21)
                if r >= config.XGB_FIRST_TRAIN_AFTER_ROUND
                and (r - config.XGB_FIRST_TRAIN_AFTER_ROUND) % config.XGB_RETRAIN_EVERY_N_ROUNDS == 0]
    assert [r for r in range(1, 21) if should_retrain(r)] == expected
    assert expected[0] == config.XGB_FIRST_TRAIN_AFTER_ROUND


def _labeled_round(round: int):
    pop = Population(42)
    legit = generate_legit_traffic("bench", round, 200, 42, pop)
    attackers = held_out_attackers()[:2]
    attacks = [tx for a in attackers for tx in a.generate("bench", round, 0, 10, 42)]
    txs = [lt.tx for lt in legit] + attacks
    features = build_features(txs, pop.identity)
    truth = {lt.tx.tx_id: lt.truth for lt in legit}
    truth.update({tx.tx_id: GroundTruth(tx_id=tx.tx_id, is_fraud=True, attacker_id=tx.attacker_id) for tx in attacks})
    return [(tx, features[tx.tx_id], truth[tx.tx_id]) for tx in txs]


def test_xgboost_alone_trains_on_schedule_and_skips_held_out_and_chat_traffic():
    defense = XGBoostDefense()
    labeled = _labeled_round(1)
    chat = sample_tx("tx-chat").model_copy(update={"source": TrafficSource.ASI_ONE})
    labeled.append((chat, FeatureVector(tx_id=chat.tx_id, values={"amount_cents": 1.0}),
                    GroundTruth(tx_id=chat.tx_id, is_fraud=False)))
    defense.learn(1, labeled)
    legit_only = [x for x in labeled if x[0].source == TrafficSource.LEGIT]
    assert len(defense.history) == len(legit_only), "held-out and chat-sourced payments never reach training"
    assert defense.trained_rounds == [1]
    defense.learn(2, _labeled_round(2))
    assert defense.trained_rounds == [1]  # round 2 isn't on the schedule


def test_frozen_xgboost_keeps_its_model():
    defense = XGBoostDefense()
    defense.freeze(True)
    defense.learn(1, _labeled_round(1))
    assert defense.voter.booster is None and defense.history == []
    defense.freeze(False)
    defense.learn(1, _labeled_round(1))
    assert defense.voter.version == 1


def test_static_rules_are_the_seed_rulebooks_with_hard_vetoes():
    rules = StaticRulesDefense()
    tx = sample_tx("tx-over", amount_cents=9_000, mandate=Mandate(
        mandate_id="m", principal_id="p-1", per_payment_cap_cents=3_000, daily_cap_cents=10_000))
    fv = build_features([tx])[tx.tx_id]
    decision = rules.decide(tx, fv)
    assert decision.action == Action.BLOCK and decision.vetoed_by == "mandate:over_cap"
    calm = sample_tx()
    calm_decision = rules.decide(calm, build_features([calm])[calm.tx_id])
    assert calm_decision.action == Action.ALLOW and calm_decision.vetoed_by is None


def test_static_rules_false_positive_rate_on_legit_traffic():
    # The seed rulebooks must meet M2's 2% false-positive cap on the weird-but-legit personas.
    labeled = [x for x in _labeled_round(1) if x[0].source == TrafficSource.LEGIT and not x[2].breaches_mandate]
    rules = StaticRulesDefense()
    flagged = sum(rules.decide(tx, fv).action != Action.ALLOW for tx, fv, _ in labeled)
    assert flagged / len(labeled) <= 0.02

import statistics
import time

import pytest

from bench.xgb_voter import XGBoostVoter
from contracts.conformance import check_voter, sample_tx
from contracts.schemas import FeatureVector
from data.seed import RUN_ID, build_seed

pytestmark = pytest.mark.m12


@pytest.fixture(scope="module")
def seed_examples():
    tables = build_seed()
    truth = {r["tx_id"]: r["is_fraud"] for r in tables["ground_truth"]}
    rounds = {r["tx_id"]: r["round"] for r in tables["transactions"] if r["run_id"] == RUN_ID}
    examples = [(rounds[r["tx_id"]], FeatureVector(tx_id=r["tx_id"], values=r["feature_values"]), truth[r["tx_id"]])
                for r in tables["features"]]
    return examples


def test_passes_the_voter_contract():
    check_voter(XGBoostVoter())


def test_untrained_and_single_class_voters_score_the_prior():
    voter = XGBoostVoter()
    tx = sample_tx()
    assert voter.score(tx, FeatureVector(tx_id=tx.tx_id, values={"amount_cents": 1.0})).score == 0.0
    voter.retrain([(FeatureVector(tx_id="a", values={"amount_cents": 1.0}), True)])
    assert voter.booster is None and voter.score(tx, FeatureVector(tx_id=tx.tx_id)).score == 1.0


def test_learns_on_seed_features_and_generalizes_to_later_rounds(seed_examples):
    voter = XGBoostVoter()
    voter.retrain([(fv, y) for r, fv, y in seed_examples if r <= 6])
    test = [(fv, y) for r, fv, y in seed_examples if r > 6]
    scores = voter.predict([fv.values for fv, _ in test])
    fraud = [s for s, (_, y) in zip(scores, test) if y]
    legit = [s for s, (_, y) in zip(scores, test) if not y]
    assert statistics.mean(fraud) > statistics.mean(legit) + 0.3
    assert voter.version == 1


def test_scores_in_milliseconds(seed_examples):
    voter = XGBoostVoter()
    voter.retrain([(fv, y) for _, fv, y in seed_examples])
    tx = sample_tx()
    fv = seed_examples[0][1]
    times = []
    for _ in range(200):
        start = time.perf_counter()
        vote = voter.score(tx, fv)
        times.append(time.perf_counter() - start)
    assert vote.voter == "xgboost" and 0.0 <= vote.score <= 1.0
    assert sorted(times)[int(0.95 * len(times))] < 0.005, "p95 scoring time must be under 5 ms"


def test_retraining_is_deterministic(seed_examples):
    data = [(fv, y) for _, fv, y in seed_examples]
    a, b = XGBoostVoter(), XGBoostVoter()
    a.retrain(data)
    b.retrain(data)
    rows = [fv.values for fv, _ in data[:100]]
    assert (a.predict(rows) == b.predict(rows)).all()

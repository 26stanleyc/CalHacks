"""The orchestrator's learned combiner (engine/learning/stacking.py) and the anomaly voter
(engine/anomaly.py)."""

import time

import numpy as np
import pytest

from contracts.schemas import Action, FeatureVector, Transaction, TrafficSource, Vote
from engine.anomaly import AnomalyVoter
from engine.learning import stacking
from engine.orchestrator import CostMatrix, Orchestrator


def _vote(voter: str, score: float) -> Vote:
    return Vote(tx_id="t", voter=voter, score=score, vote=Action.ALLOW)


def _rounds(n_rounds: int, seed: int = 0):
    """Fraud shows up as two *mild* signals together; either alone is honest noise."""
    rng = np.random.default_rng(seed)
    rounds = []
    for _ in range(n_rounds):
        rnd = []
        for _ in range(300):
            fraud = rng.random() < 0.3
            a = rng.uniform(0.3, 0.5) if fraud else rng.choice([rng.uniform(0.3, 0.5), rng.uniform(0, 0.1)])
            b = rng.uniform(0.3, 0.5) if fraud else (0.0 if a > 0.2 else rng.uniform(0.3, 0.5))
            rnd.append(([_vote("timing", a), _vote("graph", b)], 5_000, bool(fraud)))
        rounds.append(rnd)
    return rounds


def _honest(rounds):
    return [[(votes, amount) for votes, amount, y in rnd if not y] for rnd in rounds]


def test_combiner_waits_for_enough_data():
    few = [_rounds(1)[0][:20]]  # under MIN_PER_CLASS of each class
    assert stacking.train(few, _honest(few), CostMatrix(), 0.012, 1) is None
    assert stacking.train([], [], CostMatrix(), 0.012, 1) is None


def test_combiner_keeps_honest_payments_under_the_target():
    rounds = _rounds(4)
    combiner = stacking.train(rounds, _honest(rounds[-2:]), CostMatrix(), 0.012, 1)
    assert combiner is not None
    costs = CostMatrix()
    flagged = [costs.expected_costs(combiner.probability(v, a), a) for v, a in _honest(rounds[-1:])[0]]
    assert sum(min(c, key=c.get) != Action.ALLOW for c in flagged) / len(flagged) <= 0.012


def test_combiner_learns_that_two_mild_signals_together_mean_fraud():
    rounds = _rounds(4)
    combiner = stacking.train(rounds, _honest(rounds[-2:]), CostMatrix(), 0.012, 1)
    both = [_vote("timing", 0.4), _vote("graph", 0.4)]
    one = [_vote("timing", 0.4), _vote("graph", 0.0)]
    assert combiner.probability(both, 5_000) > 0.6 and combiner.probability(one, 5_000) < 0.1
    # the fixed noisy-OR barely tells them apart (0.64 vs 0.40)
    noisy = Orchestrator()
    assert noisy.combined_score(both) - noisy.combined_score(one) < 0.3


def test_combiner_never_breaks_the_cap_on_its_calibration_rounds():
    rounds = _rounds(4)
    everything_suspicious = [[([_vote("timing", 0.45), _vote("graph", 0.45)], 5_000)] * 50]
    combiner = stacking.train(rounds, everything_suspicious, CostMatrix(), 0.012, 1)
    if combiner is not None:  # either it sits out, or it's shifted until these honest payments pass
        costs = CostMatrix().expected_costs(combiner.probability(everything_suspicious[0][0][0], 5_000), 5_000)
        assert min(costs, key=costs.get) == Action.ALLOW


def _examples(n: int, seed: int = 0, shift: float = 0.0):
    rng = np.random.default_rng(seed)
    cols = [f"f{i}" for i in range(12)]
    return [(FeatureVector(tx_id=f"{seed}-{i}", values={c: float(v) for c, v in zip(cols, rng.normal(shift, 1, 12))}), False)
            for i in range(n)]


def _tx(tx_id: str) -> Transaction:
    return Transaction(tx_id=tx_id, run_id="r", round=1, ts=0.0, agent_id="a", principal_id="p", merchant_id="m",
                       amount_cents=100, source=TrafficSource.LEGIT)


def test_anomaly_voter_matches_scikit_learn_and_is_fast():
    voter = AnomalyVoter()
    examples = _examples(2000)
    voter.retrain(examples)
    X = voter._matrix([fv.values for fv, _ in examples[:300]])
    ours = -voter._mean_path_length(X)
    sklearn = -voter.model.score_samples(X)
    assert np.corrcoef(np.argsort(np.argsort(ours)), np.argsort(np.argsort(sklearn)))[0, 1] > 0.999
    start = time.perf_counter()
    for fv, _ in examples[:500]:
        voter.score(_tx(fv.tx_id), fv)
    assert (time.perf_counter() - start) / 500 < 0.002  # well under the 50 ms hot-path budget


def test_anomaly_voter_flags_unusual_payments_and_ignores_fraud_labels():
    voter = AnomalyVoter()
    assert voter.score(_tx("x"), _examples(1)[0][0]).score == 0.0  # untrained: silent
    honest = _examples(2000)
    poisoned = [(fv, True) for fv, _ in _examples(500, seed=5, shift=8.0)]  # fraud never trains it
    voter.retrain(honest + poisoned)
    normal = [voter.score(_tx(fv.tx_id), fv).score for fv, _ in _examples(300, seed=1)]
    odd = [voter.score(_tx(fv.tx_id), fv).score for fv, _ in _examples(100, seed=2, shift=6.0)]
    assert np.median(normal) < 0.8 and min(odd) > 0.95
    assert sum(s >= 0.99 for s in normal) / len(normal) < 0.05


def _records(n_honest: int, flagged: int):
    from contracts.schemas import Decision, GroundTruth
    from engine.learning.labels import Record

    out = []
    for i in range(n_honest):
        tx = _tx(f"h{i}")
        action = Action.BLOCK if i < flagged else Action.ALLOW
        out.append(Record(tx=tx, features=FeatureVector(tx_id=tx.tx_id), truth=GroundTruth(tx_id=tx.tx_id, is_fraud=False),
                          decision=Decision(tx_id=tx.tx_id, action=action, weighted_score=0.5, votes=[]), settled=False))
    return out


def test_the_guard_tightens_after_a_near_breach_and_relaxes_slowly():
    from engine.brains import Brain, MockBrains
    from engine.learning.loop import STACK_GUARD_MIN, LearningLoop
    from engine.pipeline import DefensePipeline
    from engine.store import MemoryStore

    loop = LearningLoop(DefensePipeline(), Brain(MockBrains()), MemoryStore(), "guard-test")
    loop._guard_feedback(_records(300, 9))  # no combiner deployed yet: nothing to judge
    assert loop.stack_margin == 1.0
    loop.live.orchestrator.combiner = object()
    loop._guard_feedback(_records(300, 6))  # 2%: at the cap, over the 75% line
    assert loop.stack_margin == pytest.approx(0.6)
    for _ in range(10):
        loop._guard_feedback(_records(300, 9))  # 3%: keeps tightening, down to the floor
    assert loop.stack_margin == STACK_GUARD_MIN
    loop._guard_feedback(_records(300, 0))  # a quiet round relaxes it a little
    assert STACK_GUARD_MIN < loop.stack_margin < 0.3

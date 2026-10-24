"""Benchmark baselines (R5, spec 5.1), each a `contracts.interfaces.Defense`.

- `XGBoostDefense` ("xgboost"): XGBoost alone, retrained on all labels so far on the fixed
  schedule in bench/config.py.
- `StaticRulesDefense` ("static_rules"): the engine's seed rulebooks and orchestrator (hard vetoes
  and cost matrix included), never updated.
"""

from bench import config
from bench.held_out import HELD_OUT_IDS
from bench.xgb_voter import XGBoostVoter
from contracts.schemas import Decision, FeatureVector, GroundTruth, TrafficSource, Transaction
from engine.pipeline import DefensePipeline


def trainable(tx: Transaction) -> bool:
    """Chat-sourced and held-out transactions never reach learning (spec 3.7, 4)."""
    return tx.source != TrafficSource.ASI_ONE and tx.attacker_id not in HELD_OUT_IDS


def should_retrain(round: int) -> bool:
    first, every = config.XGB_FIRST_TRAIN_AFTER_ROUND, config.XGB_RETRAIN_EVERY_N_ROUNDS
    return round >= first and (round - first) % every == 0


class XGBoostDefense:
    name = "xgboost"

    def __init__(self, voter: XGBoostVoter | None = None):
        self.voter = voter or XGBoostVoter()
        self.history: list[tuple[FeatureVector, bool]] = []
        self.frozen = False
        self.trained_rounds: list[int] = []

    def decide(self, tx: Transaction, features: FeatureVector) -> Decision:
        vote = self.voter.score(tx, features)
        return Decision(tx_id=tx.tx_id, action=vote.vote, weighted_score=vote.score, votes=[vote],
                        weights={vote.voter: 1.0}, explanation=vote.rationale or "")

    def learn(self, round: int, labeled: list[tuple[Transaction, FeatureVector, GroundTruth]]) -> None:
        if self.frozen:
            return  # frozen: labels are not even kept, so unfreezing can't replay the frozen stretch
        self.history.extend((fv, truth.is_fraud) for tx, fv, truth in labeled if trainable(tx))
        if should_retrain(round):
            self.voter.retrain(self.history)
            self.trained_rounds.append(round)

    def freeze(self, on: bool) -> None:
        self.frozen = on


class StaticRulesDefense:
    name = "static_rules"

    def __init__(self, pipeline: DefensePipeline | None = None):
        self.pipeline = pipeline or DefensePipeline()
        self.frozen = False

    def decide(self, tx: Transaction, features: FeatureVector) -> Decision:
        return self.pipeline.score(tx, features)

    def learn(self, round: int, labeled: list[tuple[Transaction, FeatureVector, GroundTruth]]) -> None:
        pass  # static: never updated

    def freeze(self, on: bool) -> None:
        self.frozen = on

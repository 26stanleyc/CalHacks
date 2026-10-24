"""Benchmark baselines (R5, spec 5.1), each a `contracts.interfaces.Defense`.

- `XGBoostDefense` ("xgboost"): XGBoost alone, retrained on all labels so far on the fixed
  schedule in bench/config.py.
- `StaticRulesDefense` ("static_rules"): a rules engine that never updates.
  PLACEHOLDER RULES: Stanley's seed rulebooks (engine/, M2) don't exist yet. Swap PLACEHOLDER_RULES
  for them once they land, so this baseline is "the seed rulebooks, never updated" as the spec says.
"""

from collections.abc import Callable
from dataclasses import dataclass

from bench import config
from bench.held_out import HELD_OUT_IDS
from bench.xgb_voter import XGBoostVoter, action_for
from contracts.schemas import (
    Action,
    Decision,
    FeatureVector,
    GroundTruth,
    TrafficSource,
    Transaction,
    Vote,
)


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


@dataclass(frozen=True)
class Rule:
    rule_id: str
    when: Callable[[dict[str, float]], bool]
    score: float
    veto: bool = False  # a hard block, like the engine's mandate and identity vetoes


def _f(values: dict[str, float], name: str, default: float = 0.0) -> float:
    return values.get(name, default)


# PLACEHOLDER: replace with Stanley's seed rulebooks when they exist (see module docstring).
PLACEHOLDER_RULES: list[Rule] = [
    Rule("mandate.over_cap", lambda v: _f(v, "mandate_over_cap_ratio") > 1.0, 0.9, veto=True),
    Rule("mandate.principal_mismatch", lambda v: _f(v, "mandate_principal_match", 1.0) < 1.0, 0.9, veto=True),
    Rule("identity.failed", lambda v: _f(v, "identity_failed") >= 1.0, 0.9, veto=True),
    Rule("mandate.memo_injection", lambda v: _f(v, "memo_injection_score") >= 0.6, 0.9, veto=True),
    Rule("mandate.category_not_allowed", lambda v: _f(v, "mandate_category_allowed", 1.0) < 1.0, 0.6),
    Rule("mandate.price_over_listed", lambda v: _f(v, "price_over_listed_ratio") > 0.10, 0.6),
    Rule("velocity.many_tx_10m", lambda v: _f(v, "count_tx_per_agent_10m") >= 5, 0.5),
    Rule("velocity.many_merchants_1h", lambda v: _f(v, "distinct_merchants_per_agent_1h") >= 4, 0.4),
    Rule("identity.thin_file_high_amount",
         lambda v: _f(v, "identity_thin_file") >= 1.0 and _f(v, "amount_cents") >= 10_000, 0.5),
    Rule("graph.many_principals_24h", lambda v: _f(v, "distinct_principals_per_agent_24h") >= 15, 0.3),
    Rule("timing.new_agent_high_amount",
         lambda v: _f(v, "agent_first_seen") >= 1.0 and _f(v, "amount_cents") >= 20_000, 0.4),
    Rule("velocity.large_amount", lambda v: _f(v, "amount_cents") >= 50_000, 0.3),
]


class StaticRulesDefense:
    name = "static_rules"

    def __init__(self, rules: list[Rule] | None = None):
        self.rules = list(rules or PLACEHOLDER_RULES)
        self.frozen = False

    def decide(self, tx: Transaction, features: FeatureVector) -> Decision:
        fired = [r for r in self.rules if r.when(features.values)]
        score = min(1.0, sum(r.score for r in fired))
        veto = next((r for r in fired if r.veto), None)
        action = Action.BLOCK if veto else action_for(score)
        vote = Vote(tx_id=tx.tx_id, voter=self.name, score=score, vote=action, rule_ids=[r.rule_id for r in fired])
        return Decision(tx_id=tx.tx_id, action=action, weighted_score=score, votes=[vote], weights={self.name: 1.0},
                        vetoed_by=veto.rule_id if veto else None,
                        explanation=f"rules fired: {', '.join(vote.rule_ids)}" if fired else "no rules fired")

    def learn(self, round: int, labeled: list[tuple[Transaction, FeatureVector, GroundTruth]]) -> None:
        pass  # static: never updated

    def freeze(self, on: bool) -> None:
        self.frozen = on

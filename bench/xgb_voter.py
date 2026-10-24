"""The XGBoost voter (R5, spec 3.3): one vote next to the specialists, weighed by the orchestrator.

Retrained between rounds on (features, is_fraud). Scoring one transaction uses
`Booster.inplace_predict` on a single numpy row, which takes well under a millisecond.
It never writes rules and never calls an LLM.
"""

import numpy as np
import xgboost as xgb

from bench import config
from contracts.schemas import Action, FeatureVector, Transaction, Vote


def action_for(score: float) -> Action:
    if score >= config.BLOCK_AT:
        return Action.BLOCK
    return Action.STEP_UP if score >= config.STEP_UP_AT else Action.ALLOW


class XGBoostVoter:
    name = "xgboost"

    def __init__(self, params: dict | None = None):
        self.params = {**config.XGB_PARAMS, **(params or {})}
        self.booster: xgb.Booster | None = None
        self.columns: list[str] = []
        self.prior = 0.0  # fraud share in the last training set; the score until a model exists
        self.version = 0  # bumps on every retrain, so logged votes say which model cast them

    def _matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        return np.array([[r.get(c, np.nan) for c in self.columns] for r in rows], dtype=np.float32)

    def predict(self, rows: list[dict[str, float]]) -> np.ndarray:
        """Fraud probabilities for many feature dicts at once (used by the baselines)."""
        if self.booster is None or not rows:
            return np.full(len(rows), self.prior, dtype=np.float32)
        return self.booster.inplace_predict(self._matrix(rows))

    def score(self, tx: Transaction, features: FeatureVector) -> Vote:
        s = float(np.clip(self.predict([features.values])[0], 0.0, 1.0))
        return Vote(tx_id=tx.tx_id, voter=self.name, score=s, vote=action_for(s),
                    rationale=f"xgboost v{self.version}: p(fraud)={s:.2f}" if self.booster else "xgboost: not trained yet")

    def retrain(self, examples: list[tuple[FeatureVector, bool]]) -> None:
        if not examples:
            return
        labels = np.array([bool(y) for _, y in examples], dtype=np.int32)
        self.columns = sorted({name for fv, _ in examples for name in fv.values})
        self.prior = float(labels.mean())
        self.version += 1
        if labels.min() == labels.max():
            self.booster = None  # one class only: nothing to separate yet, so score the prior
            return
        model = xgb.XGBClassifier(objective="binary:logistic", eval_metric="logloss", **self.params)
        model.fit(self._matrix([fv.values for fv, _ in examples]), labels)
        self.booster = model.get_booster()

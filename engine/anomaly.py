"""The anomaly voter: flags payments unlike honest traffic, for fraud nobody has labeled yet.

XGBoost and the rulebooks learn from fraud they've seen, so a brand-new pattern slips past them.
This voter learns what *honest* payments look like (an Isolation Forest on the recent honest
payments' features) and scores how unusual a payment is. No fraud labels are needed, so it can
react to a pattern on first contact.

Its score is the payment's percentile among the honest training payments' unusualness: 0.99 means
it's more unusual than 99% of honest traffic. It votes step-up only in the top 1% and block in the
top 0.1%; the orchestrator's learned combiner decides how much the score is worth next to the others.
Retrained between rounds like the XGBoost voter; it never writes rules and never calls an LLM.

Speed: scikit-learn's per-call overhead is about 7 ms a payment, too slow for the arena's tens of
thousands of practice payments a round, so the fitted trees are copied into arrays and walked for all
trees at once (same path lengths, about 0.1 ms a payment). A test checks it matches scikit-learn.
"""

import numpy as np
from sklearn.ensemble import IsolationForest

from contracts.schemas import Action, FeatureVector, Transaction, Vote

MAX_HONEST = 4000  # most recent honest payments it trains on
MISSING = -1.0  # a missing feature stays visible as missing (e.g. an agent with no baseline yet)


class AnomalyVoter:
    name = "anomaly"

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.model: IsolationForest | None = None
        self.columns: list[str] = []
        self.reference = np.array([])  # sorted unusualness of the honest training payments
        self.version = 0
        self._trees: tuple | None = None  # (left, right, feature, threshold, leaf_length, max_depth)

    def _matrix(self, rows: list[dict[str, float]]) -> np.ndarray:
        return np.array([[r.get(c, MISSING) for c in self.columns] for r in rows], dtype=np.float32)

    def percentile(self, rows: list[dict[str, float]]) -> np.ndarray:
        if self.model is None or not rows:
            return np.zeros(len(rows), dtype=np.float32)
        unusual = -self._mean_path_length(self._matrix(rows))  # shorter paths = easier to isolate
        return np.searchsorted(self.reference, unusual, side="right") / len(self.reference)

    def _mean_path_length(self, X: np.ndarray) -> np.ndarray:
        left, right, feature, threshold, leaf_length, max_depth = self._trees
        t = np.arange(left.shape[0])[None, :]
        node = np.zeros((X.shape[0], left.shape[0]), dtype=np.int64)
        rows = np.arange(X.shape[0])[:, None]
        for _ in range(max_depth):
            f = feature[t, node]
            go_left = X[rows, np.maximum(f, 0)] <= threshold[t, node]
            nxt = np.where(go_left, left[t, node], right[t, node])
            node = np.where(nxt >= 0, nxt, node)  # leaves have no children: stay
        return leaf_length[t, node].mean(axis=1)

    def score(self, tx: Transaction, features: FeatureVector) -> Vote:
        s = float(self.percentile([features.values])[0])
        vote = Action.BLOCK if s >= 0.999 else Action.STEP_UP if s >= 0.99 else Action.ALLOW
        return Vote(tx_id=tx.tx_id, voter=self.name, score=s, vote=vote,
                    rationale=f"anomaly v{self.version}: more unusual than {s:.1%} of honest payments"
                    if self.model else "anomaly: not trained yet")

    def retrain(self, examples: list[tuple[FeatureVector, bool]]) -> None:
        honest = [fv.values for fv, is_fraud in examples if not is_fraud][-MAX_HONEST:]
        if len(honest) < 50:
            return
        self.columns = sorted({name for row in honest for name in row})
        X = self._matrix(honest)
        self.model = IsolationForest(n_estimators=100, random_state=self.seed).fit(X)
        self._trees = _tree_arrays(self.model)
        self.reference = np.sort(-self._mean_path_length(X))
        self.version += 1


def _average_path_length(n: np.ndarray) -> np.ndarray:
    """Expected path length of an unsuccessful search in a binary tree of n points (Liu et al. 2008)."""
    n = np.asarray(n, dtype=np.float64)
    out = np.zeros_like(n)
    out[n == 2] = 1.0
    big = n > 2
    out[big] = 2.0 * (np.log(n[big] - 1.0) + np.euler_gamma) - 2.0 * (n[big] - 1.0) / n[big]
    return out


def _tree_arrays(model: IsolationForest) -> tuple:
    trees = [est.tree_ for est in model.estimators_]
    size = max(t.node_count for t in trees)
    shape = (len(trees), size)
    left, right = np.full(shape, -1, dtype=np.int64), np.full(shape, -1, dtype=np.int64)
    feature, threshold = np.zeros(shape, dtype=np.int64), np.zeros(shape, dtype=np.float64)
    leaf_length = np.zeros(shape, dtype=np.float64)
    max_depth = 0
    for i, (tree, used) in enumerate(zip(trees, model.estimators_features_)):
        n = tree.node_count
        left[i, :n], right[i, :n] = tree.children_left, tree.children_right
        feature[i, :n] = np.where(tree.feature >= 0, np.asarray(used)[np.maximum(tree.feature, 0)], -1)
        threshold[i, :n] = tree.threshold
        depth = np.zeros(n, dtype=np.int64)
        for node in range(n):  # children always come after their parent
            for child in (tree.children_left[node], tree.children_right[node]):
                if child >= 0:
                    depth[child] = depth[node] + 1
        leaf_length[i, :n] = depth + _average_path_length(tree.n_node_samples)
        max_depth = max(max_depth, int(depth.max()))
    return left, right, feature, threshold, leaf_length, max_depth

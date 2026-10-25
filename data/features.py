"""Feature vectors for Ryan's side (seed data, M1 checks, XGBoost and baselines).

These are the engine's own features (engine/features.py, spec 3.2), so seeded rows, benchmark
inputs and real rounds all use the same names. `identity` is a lookup like `Population.identity`.
"""

from collections.abc import Callable, Iterable

from contracts.schemas import FeatureVector, IdentityStatus, Transaction
from engine import features as engine_features


def build_features(txs: Iterable[Transaction],
                   identity: Callable[[str], IdentityStatus | None] | None = None) -> dict[str, FeatureVector]:
    txs = list(txs)
    lookup = identity or (lambda _pid: None)
    identities = {pid: status for pid in {tx.principal_id for tx in txs} if (status := lookup(pid)) is not None}
    return engine_features.build_features(txs, identities)

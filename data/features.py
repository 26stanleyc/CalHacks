"""Placeholder feature vectors for Ryan's side (seed data, M1 checks, XGBoost and baselines).

PLACEHOLDER: Stanley's feature service (spec 3.2, engine/) is the real source of features. This
builder only exists so data/ and bench/ can run alone before I1. It uses the same style of names
(field x window x aggregate) and reads nothing but the Transaction and an identity lookup, so it
can be swapped for the engine's `features` table rows without changing the voter or baselines.
"""

import math
import re
from collections import defaultdict, deque
from collections.abc import Callable, Iterable

from contracts.schemas import FeatureVector, IdentityStatus, Transaction

_INJECTION = re.compile(
    r"ignore (all |your |previous |prior )*(rules|instructions)|approve this|pre-?approved|override|"
    r"system prompt|you are now|disregard|treat as (within|inside) (the )?mandate|pay (this|the) other address",
    re.IGNORECASE,
)

DAY = 86_400.0


def injection_score(text: str) -> float:
    """Keyword heuristic in [0, 1]; the engine's injection detector replaces it."""
    if not text:
        return 0.0
    hits = len(_INJECTION.findall(text))
    return min(1.0, 0.6 * hits + (0.2 if len(text) > 160 else 0.0))


class FeatureBuilder:
    """Streams transactions in time order and keeps rolling per-agent and per-principal windows."""

    def __init__(self, identity: Callable[[str], IdentityStatus | None] | None = None):
        self.identity = identity or (lambda _pid: None)
        self.agent_events: dict[str, deque] = defaultdict(deque)  # (ts, merchant, principal)
        self.principal_agents: dict[str, deque] = defaultdict(deque)  # (ts, agent)
        self.principal_spend: dict[tuple[str, int], int] = defaultdict(int)  # (principal, day) -> cents
        self.agent_merchants: dict[str, set[str]] = defaultdict(set)

    def add(self, tx: Transaction) -> FeatureVector:
        ts = tx.ts
        events = self.agent_events[tx.agent_id]
        while events and ts - events[0][0] > DAY:
            events.popleft()
        agents = self.principal_agents[tx.principal_id]
        while agents and ts - agents[0][0] > DAY:
            agents.popleft()

        last_10m = [e for e in events if ts - e[0] <= 600]
        last_1h = [e for e in events if ts - e[0] <= 3600]
        day = int(ts // DAY)
        spent_today = self.principal_spend[(tx.principal_id, day)] + tx.amount_cents
        identity = self.identity(tx.principal_id)
        m = tx.mandate

        values = {
            "amount_cents": float(tx.amount_cents),
            "log_amount": math.log1p(tx.amount_cents),
            "hour_of_day": (ts % DAY) / 3600.0,
            "count_tx_per_agent_10m": float(len(last_10m)),
            "count_tx_per_agent_1h": float(len(last_1h)),
            "distinct_merchants_per_agent_1h": float(len({e[1] for e in last_1h} | {tx.merchant_id})),
            "distinct_principals_per_agent_24h": float(len({e[2] for e in events} | {tx.principal_id})),
            "distinct_agents_per_principal_24h": float(len({a for _, a in agents} | {tx.agent_id})),
            "seconds_since_agent_last_tx": min(DAY, ts - events[-1][0]) if events else DAY,
            "agent_first_seen": 0.0 if self.agent_merchants[tx.agent_id] else 1.0,
            "merchant_new_for_agent": 0.0 if tx.merchant_id in self.agent_merchants[tx.agent_id] else 1.0,
            "identity_verified": float(identity == IdentityStatus.VERIFIED),
            "identity_thin_file": float(identity == IdentityStatus.THIN_FILE),
            "identity_failed": float(identity in (IdentityStatus.MISMATCH, IdentityStatus.FLAGGED)),
            "identity_unknown": float(identity is None),
            "has_mandate": float(m is not None),
            "mandate_over_cap_ratio": tx.amount_cents / m.per_payment_cap_cents if m else 0.0,
            "mandate_daily_used_ratio": spent_today / m.daily_cap_cents if m else 0.0,
            "mandate_category_allowed": float(not m or not m.allowed_categories or tx.category in m.allowed_categories),
            "mandate_merchant_allowed": float(not m or not m.allowed_merchants or tx.merchant_id in m.allowed_merchants),
            "mandate_principal_match": float(not m or m.principal_id == tx.principal_id),
            "price_over_listed_ratio": (tx.amount_cents / tx.listed_price_cents - 1.0) if tx.listed_price_cents else 0.0,
            "memo_length": float(len(tx.memo)),
            "memo_injection_score": injection_score(tx.memo),
        }

        events.append((ts, tx.merchant_id, tx.principal_id))
        agents.append((ts, tx.agent_id))
        self.principal_spend[(tx.principal_id, day)] = spent_today
        self.agent_merchants[tx.agent_id].add(tx.merchant_id)
        return FeatureVector(tx_id=tx.tx_id, values=values)


def build_features(txs: Iterable[Transaction],
                   identity: Callable[[str], IdentityStatus | None] | None = None) -> dict[str, FeatureVector]:
    builder = FeatureBuilder(identity)
    return {tx.tx_id: builder.add(tx) for tx in sorted(txs, key=lambda t: (t.ts, t.tx_id))}

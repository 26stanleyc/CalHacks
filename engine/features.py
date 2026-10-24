"""Feature service (spec 3.2): keeps rolling windows per entity and turns each transaction into a
FeatureVector before fan-out. Each specialist only ever sees its own lane's slice.

Windowed features are primitives: entity x field x aggregate x window (e.g. distinct merchant_id
per agent over 600 s). Any valid combination can be added later without new code (S4 feature
requests). Static features cover what windows can't express (gaps, regularity, identity, mandate).

Never reads Transaction.attacker_id: that's simulator ground truth, hidden from the defense.
"""

import math
import re
import unicodedata
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from contracts.schemas import FeatureVector, IdentityStatus, Lane, Transaction

ENTITIES = ("agent_id", "principal_id", "merchant_id")
AGGREGATES = ("count", "sum", "mean", "max", "distinct")
CATEGORICAL_FIELDS = ("agent_id", "principal_id", "merchant_id", "product_id", "category")
NUMERIC_FIELDS = ("tx", "amount_cents", "small_amount", "near_threshold", "new_agent")
FIELDS = CATEGORICAL_FIELDS + NUMERIC_FIELDS

SMALL_AMOUNT_CENTS = 500
NEW_AGENT_SECONDS = 600
NEAR_THRESHOLD_FRAC = 0.05
THRESHOLDS_CENTS = (1_000, 5_000, 10_000, 50_000, 100_000)
NO_HISTORY = 1e6  # stand-in for "never seen before" gaps and ages
OVER_ANY_CAP = 1e9  # ratio used when a cap is 0: any positive amount is over it (finite, so it stores as JSON)
DAY_SECONDS = 86_400

IDENTITY_CODE = {IdentityStatus.VERIFIED: 0.0, IdentityStatus.THIN_FILE: 1.0,
                 IdentityStatus.MISMATCH: 2.0, IdentityStatus.FLAGGED: 3.0}
UNKNOWN_IDENTITY = 1.0  # treated like a thin file

# Each pattern needs an instruction aimed at the checker (rules, checks, fraud, this payment...),
# so ordinary shopper notes ("don't block my card", "accept the order if the box is dented") pass.
INJECTION_PATTERNS = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in (
    r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all|your|the)\s+(instructions?|rules?|guidelines?|policy|policies|limits?|budget)\b",
    r"\b(override|bypass|disable|skip|turn\s+off|ignore)\b.{0,40}\b(rules?|checks?|limits?|filters?|security|fraud|verification|reviews?|caps?|mandates?|budgets?)\b",
    r"\b(approve|allow|whitelist|authori[sz]e)\b.{0,20}\b(this|the)\s+(payment|transaction|purchase|order|charge)\b.{0,40}\b(without|regardless|no\s+matter|even\s+if\s+(it|flagged|suspicious))\b",
    r"\bsystem\s*prompt\b",
    r"\byou\s+are\s+now\s+(in\s+)?(a\s+|an\s+)?\w*\s*(mode|assistant|agent|admin|administrator|developer|reviewer)\b",
    r"\b(do\s+not|don'?t|never)\s+(flag|block|report|review|check)\s+(this|the|my|our)\s+(payment|transaction|purchase|order|charge)\b",
    r"\bas\s+an?\s+(ai|assistant|agent|language\s+model)\b.{0,40}\b(you\s+)?(must|should|have\s+to|are\s+required\s+to)\b",
    r"\b(note|message|instruction)s?\s+(to|for)\s+(the\s+)?(payment\s+)?(reviewer|checker|fraud|security|ai|agent|model)\b",
    r"\b(treat|treated|consider|considered|count|counted|mark|marked)\b.{0,30}\b(within|inside|under)\s+(the\s+|its\s+|my\s+)?(mandate|budget|limit|cap)\b",
    r"(?:^|[\s\[(])(system|assistant|admin|administrator|developer)\s*:\s",
)]
ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def normalize_text(text: str) -> str:
    """Undo cheap obfuscation: full-width letters and compatibility forms (NFKC), zero-width
    characters and soft hyphens, and runs of whitespace."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").translate(ZERO_WIDTH))


def injection_score(text: str) -> float:
    clean = normalize_text(text)
    hits = sum(1 for p in INJECTION_PATTERNS if p.search(clean))
    return 0.0 if hits == 0 else 0.7 if hits == 1 else 0.95


def threshold_gap_frac(amount_cents: int) -> float:
    """How far below the nearest round-number threshold the amount sits (small = just under)."""
    gaps = [(t - amount_cents) / t for t in THRESHOLDS_CENTS if amount_cents < t]
    return min(gaps) if gaps else 1.0


@dataclass(frozen=True)
class Primitive:
    entity: str
    field: str
    aggregate: str
    window_s: int

    def validate(self) -> None:
        if self.entity not in ENTITIES:
            raise ValueError(f"unknown entity {self.entity!r}")
        if self.field not in FIELDS:
            raise ValueError(f"unknown field {self.field!r}")
        if self.aggregate not in AGGREGATES:
            raise ValueError(f"unknown aggregate {self.aggregate!r}")
        if self.aggregate == "distinct" and self.field not in CATEGORICAL_FIELDS:
            raise ValueError("distinct needs a categorical field")
        if self.aggregate in ("sum", "mean", "max") and self.field not in NUMERIC_FIELDS:
            raise ValueError(f"{self.aggregate} needs a numeric field")
        if not 1 <= self.window_s <= DAY_SECONDS:
            raise ValueError("window must be 1 s to 1 day")


STATIC_FEATURES: dict[str, Lane] = {
    "t_gap_prev_s": Lane.TIMING,
    "t_gap_cv_agent": Lane.TIMING,
    "t_agent_age_s": Lane.TIMING,
    "b_memo_injection": Lane.TIMING,  # timing/behavior specialist owns free-text behavior
    "v_amount_cents": Lane.VELOCITY,
    "v_threshold_gap_frac": Lane.VELOCITY,
    "v_price_ratio": Lane.VELOCITY,
    "g_agents_per_principal": Lane.GRAPH,
    "g_principals_per_agent": Lane.GRAPH,
    "g_agent_new": Lane.GRAPH,  # the paying agent itself was first seen under NEW_AGENT_SECONDS ago
    "i_identity_status": Lane.IDENTITY,
    "i_principal_age_s": Lane.IDENTITY,
    "m_over_cap_ratio": Lane.MANDATE,
    "m_daily_spend_ratio": Lane.MANDATE,
    "m_category_allowed": Lane.MANDATE,
    "m_merchant_allowed": Lane.MANDATE,
}

SEED_PRIMITIVES: dict[str, tuple[Lane, Primitive]] = {
    "t_count_agent_60s": (Lane.TIMING, Primitive("agent_id", "tx", "count", 60)),
    "t_count_agent_600s": (Lane.TIMING, Primitive("agent_id", "tx", "count", 600)),
    "v_sum_agent_3600s": (Lane.VELOCITY, Primitive("agent_id", "amount_cents", "sum", 3_600)),
    "v_small_count_agent_600s": (Lane.VELOCITY, Primitive("agent_id", "small_amount", "sum", 600)),
    "v_near_threshold_count_agent_3600s": (Lane.VELOCITY, Primitive("agent_id", "near_threshold", "sum", 3_600)),
    "v_distinct_merchants_agent_600s": (Lane.VELOCITY, Primitive("agent_id", "merchant_id", "distinct", 600)),
    "v_count_principal_3600s": (Lane.VELOCITY, Primitive("principal_id", "tx", "count", 3_600)),
    "g_principals_per_merchant_600s": (Lane.GRAPH, Primitive("merchant_id", "principal_id", "distinct", 600)),
    "g_new_agent_tx_merchant_600s": (Lane.GRAPH, Primitive("merchant_id", "new_agent", "sum", 600)),
}


class FeatureCatalog:
    """Which features exist and which lane owns each. Lanes are sets of feature names."""

    def __init__(self):
        self.lanes: dict[str, Lane] = dict(STATIC_FEATURES)
        self.primitives: dict[str, Primitive] = {}
        for name, (lane, primitive) in SEED_PRIMITIVES.items():
            self.add_primitive(name, lane, primitive)

    def add_primitive(self, name: str, lane: Lane, primitive: Primitive) -> None:
        if name in self.lanes:
            raise ValueError(f"feature {name!r} already exists")
        primitive.validate()
        self.lanes[name] = lane
        self.primitives[name] = primitive

    def lane_features(self, lane: Lane) -> list[str]:
        return sorted(n for n, owner in self.lanes.items() if owner == lane)

    def owner(self, name: str) -> Lane | None:
        return self.lanes.get(name)


class FeatureService:
    """Stateful: transactions must be fed in time order. `commit=False` computes features without
    remembering the transaction (chat-submitted checks must not change the defense's state)."""

    def __init__(self, catalog: FeatureCatalog | None = None, identities: dict[str, IdentityStatus] | None = None):
        self.catalog = catalog or FeatureCatalog()
        self.identities = identities if identities is not None else {}
        self.events: dict[str, dict[str, deque]] = {e: defaultdict(deque) for e in ENTITIES}
        self.first_seen_agent: dict[str, float] = {}
        self.first_seen_principal: dict[str, float] = {}
        self.last_ts_agent: dict[str, float] = {}
        self.gaps_agent: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))
        self.agents_of_principal: dict[str, set] = defaultdict(set)
        self.principals_of_agent: dict[str, set] = defaultdict(set)
        self.spend_principal: dict[str, deque] = defaultdict(deque)
        self.clock = 0.0  # latest committed transaction time


    def _max_window(self, entity: str) -> int:
        windows = [p.window_s for p in self.catalog.primitives.values() if p.entity == entity]
        return max(windows, default=0)

    def _record(self, tx: Transaction) -> dict:
        first = self.first_seen_agent.get(tx.agent_id)
        return {
            "ts": tx.ts, "tx": 1.0, "amount_cents": float(tx.amount_cents),
            "small_amount": 1.0 if tx.amount_cents < SMALL_AMOUNT_CENTS else 0.0,
            "near_threshold": 1.0 if threshold_gap_frac(tx.amount_cents) < NEAR_THRESHOLD_FRAC else 0.0,
            "new_agent": 1.0 if first is None or tx.ts - first < NEW_AGENT_SECONDS else 0.0,
            "agent_id": tx.agent_id, "principal_id": tx.principal_id, "merchant_id": tx.merchant_id,
            "product_id": tx.product_id, "category": tx.category,
        }

    def _aggregate(self, p: Primitive, key: str, current: dict, now: float) -> float:
        rows = [r for r in self.events[p.entity].get(key, ()) if 0 <= now - r["ts"] < p.window_s]
        rows.append(current)
        if p.aggregate == "count":
            return float(len(rows))
        if p.aggregate == "distinct":
            return float(len({r[p.field] for r in rows if r[p.field] is not None}))
        values = [r[p.field] for r in rows]
        if p.aggregate == "sum":
            return float(sum(values))
        if p.aggregate == "mean":
            return float(sum(values) / len(values))
        return float(max(values))

    def compute(self, tx: Transaction, commit: bool = True) -> FeatureVector:
        now = tx.ts
        record = self._record(tx)
        values: dict[str, float] = {}

        for name, p in self.catalog.primitives.items():
            values[name] = self._aggregate(p, record[p.entity], record, now)

        last = self.last_ts_agent.get(tx.agent_id)
        gap = now - last if last is not None and now >= last else None
        gaps = list(self.gaps_agent.get(tx.agent_id, ())) + ([gap] if gap is not None else [])
        values["t_gap_prev_s"] = gap if gap is not None else NO_HISTORY
        values["t_gap_cv_agent"] = _coefficient_of_variation(gaps) if len(gaps) >= 3 else 1.0
        first_agent = self.first_seen_agent.get(tx.agent_id)
        values["t_agent_age_s"] = now - first_agent if first_agent is not None else 0.0
        values["b_memo_injection"] = injection_score(tx.memo)

        values["v_amount_cents"] = float(tx.amount_cents)
        values["v_threshold_gap_frac"] = threshold_gap_frac(tx.amount_cents)
        values["v_price_ratio"] = tx.amount_cents / tx.listed_price_cents if tx.listed_price_cents else 1.0

        values["g_agents_per_principal"] = float(len(self.agents_of_principal.get(tx.principal_id, set()) | {tx.agent_id}))
        values["g_principals_per_agent"] = float(len(self.principals_of_agent.get(tx.agent_id, set()) | {tx.principal_id}))
        values["g_agent_new"] = record["new_agent"]

        identity = self.identities.get(tx.principal_id)
        values["i_identity_status"] = IDENTITY_CODE[identity] if identity is not None else UNKNOWN_IDENTITY
        first_principal = self.first_seen_principal.get(tx.principal_id)
        values["i_principal_age_s"] = now - first_principal if first_principal is not None else 0.0

        day = now // DAY_SECONDS  # the daily cap resets at midnight (UTC), like a card's daily limit
        spent_today = sum(a for ts, a in self.spend_principal.get(tx.principal_id, ()) if ts <= now and ts // DAY_SECONDS == day)
        m = tx.mandate
        values["m_over_cap_ratio"] = _cap_ratio(tx.amount_cents, m.per_payment_cap_cents) if m else 0.0
        values["m_daily_spend_ratio"] = _cap_ratio(spent_today + tx.amount_cents, m.daily_cap_cents) if m else 0.0
        values["m_category_allowed"] = 0.0 if m and m.allowed_categories and tx.category not in m.allowed_categories else 1.0
        values["m_merchant_allowed"] = 0.0 if m and m.allowed_merchants and tx.merchant_id not in m.allowed_merchants else 1.0

        if commit:
            self._commit(tx, record, gap)
        return FeatureVector(tx_id=tx.tx_id, values=values)

    def _commit(self, tx: Transaction, record: dict, gap: float | None) -> None:
        now = tx.ts
        self.clock = max(self.clock, now)
        for entity in ENTITIES:
            window = self._max_window(entity)
            rows = self.events[entity][record[entity]]
            rows.append(record)
            while rows and now - rows[0]["ts"] >= window:
                rows.popleft()
        self.first_seen_agent.setdefault(tx.agent_id, now)
        self.first_seen_principal.setdefault(tx.principal_id, now)
        if gap is not None:
            self.gaps_agent[tx.agent_id].append(gap)
        self.last_ts_agent[tx.agent_id] = now
        self.agents_of_principal[tx.principal_id].add(tx.agent_id)
        self.principals_of_agent[tx.agent_id].add(tx.principal_id)

    def record_spend(self, tx: Transaction) -> None:
        """Count a payment toward its principal's daily spend. Call only once money actually moved:
        blocked or abandoned attempts must not use up the human's daily limit."""
        spend = self.spend_principal[tx.principal_id]
        spend.append((tx.ts, tx.amount_cents))
        while spend and tx.ts - spend[0][0] >= DAY_SECONDS:
            spend.popleft()


def build_features(txs: Iterable[Transaction], identities: dict[str, IdentityStatus] | None = None,
                   catalog: FeatureCatalog | None = None) -> dict[str, FeatureVector]:
    """Features for a batch of payments, fed in time order: what benchmarks give every contender
    (spec 5.1) and what seeded rows carry. With no defense deciding, every payment within its
    mandate's caps counts as settled toward its principal's daily spend."""
    service = FeatureService(catalog, identities)
    out: dict[str, FeatureVector] = {}
    for tx in sorted(txs, key=lambda t: (t.ts, t.tx_id)):
        features = out[tx.tx_id] = service.compute(tx, commit=True)
        if features.values["m_over_cap_ratio"] <= 1.0 and features.values["m_daily_spend_ratio"] <= 1.0:
            service.record_spend(tx)
    return out


def _cap_ratio(amount_cents: int, cap_cents: int) -> float:
    if cap_cents > 0:
        return amount_cents / cap_cents
    return OVER_ANY_CAP if amount_cents > 0 else 0.0  # a cap of 0 allows nothing


def _coefficient_of_variation(values: list[float]) -> float:
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance) / mean

"""Data formats passed between the engine, integrations, data and dashboard.

Money is integer cents of USDC. Times are simulation seconds since the run started.
Ground truth (is this fraud?) never travels with a transaction; it lives in GroundTruth,
so nothing on the defense side can read it by accident.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


# --- Enums -------------------------------------------------------------------------


class Action(StrEnum):
    ALLOW = "allow"
    STEP_UP = "step_up"
    BLOCK = "block"


class Lane(StrEnum):
    """A specialist's area. Each specialist only sees its own lane's features."""

    TIMING = "timing"
    GRAPH = "graph"
    IDENTITY = "identity"
    VELOCITY = "velocity"
    MANDATE = "mandate"


class AttackStyle(StrEnum):
    BURST = "burst"
    SLOW_DRIP = "slow_drip"
    MANY_ACCOUNTS = "many_accounts"
    HIJACK_INJECTION = "hijack_injection"


class AmountBand(StrEnum):
    UNDER_10 = "under_10"
    FROM_10_TO_100 = "10_to_100"
    OVER_100 = "over_100"


class Side(StrEnum):
    """Who the attack targets: merchants (bad buyers) or buyers (bad merchants)."""

    MERCHANT = "merchant"
    BUYER = "buyer"


class AttackerSource(StrEnum):
    SPAWNED = "spawned"  # insider: built by a defender, sees its parent's rules (gray-box)
    OUTSIDER = "outsider"  # named lineage: sees outcomes only
    THREAT_INTEL = "threat_intel"  # built from a scraped scam report; sees outcomes only
    HELD_OUT = "held_out"  # never trained against; frozen exam only


class IdentityStatus(StrEnum):
    VERIFIED = "verified"
    THIN_FILE = "thin_file"
    MISMATCH = "mismatch"
    FLAGGED = "flagged"


class TrafficSource(StrEnum):
    LEGIT = "legit"
    ATTACKER = "attacker"
    ASI_ONE = "asi_one"  # chat-submitted; scored for real, never used for learning


# --- Marketplace -------------------------------------------------------------------


class Mandate(BaseModel):
    """What a human authorized their agent to do (simplified AP2 idea)."""

    mandate_id: str
    principal_id: str
    per_payment_cap_cents: int
    daily_cap_cents: int
    allowed_categories: list[str] = Field(default_factory=list)  # empty = any
    allowed_merchants: list[str] = Field(default_factory=list)  # empty = any
    request_text: str = ""  # e.g. "buy a USB-C charger under $30"


class Product(BaseModel):
    product_id: str
    merchant_id: str
    title: str
    category: str
    price_cents: int
    page_url: str | None = None


class Principal(BaseModel):
    """The simulated human behind one or more agents."""

    principal_id: str
    identity: IdentityStatus
    crs_profile_id: str | None = None  # set only if CRS access is confirmed


class Transaction(BaseModel):
    tx_id: str
    run_id: str
    round: int
    wave: int = 0
    ts: float
    agent_id: str
    principal_id: str
    merchant_id: str
    product_id: str | None = None
    category: str | None = None
    amount_cents: int
    listed_price_cents: int | None = None  # for the price-consistency check
    memo: str = ""  # attacker-controlled free text; always treated as data
    mandate: Mandate | None = None
    source: TrafficSource
    attacker_id: str | None = None  # set by the simulator; hidden from defenders


class GroundTruth(BaseModel):
    tx_id: str
    is_fraud: bool
    attacker_id: str | None = None
    breaches_mandate: bool = False


# --- Defense -----------------------------------------------------------------------


class FeatureVector(BaseModel):
    """Features for one transaction. Specialists receive only their lane's slice."""

    tx_id: str
    values: dict[str, float] = Field(default_factory=dict)

    def slice(self, names: list[str]) -> "FeatureVector":
        return FeatureVector(tx_id=self.tx_id, values={n: self.values[n] for n in names if n in self.values})


class Vote(BaseModel):
    tx_id: str
    voter: str  # a Lane value, or "xgboost"
    score: float = Field(ge=0.0, le=1.0)  # suspicion
    vote: Action
    rule_ids: list[str] = Field(default_factory=list)
    rationale: str | None = None


class ToolCall(BaseModel):
    tool: str  # graph_lookup | scan_page | identity_check | similar_cases | specialist_detail,
    # or a CRS check: crs_data_prefill | crs_identity_verification | crs_fraud_finder
    input: dict = Field(default_factory=dict)
    output_summary: str = ""


class InvestigatorReport(BaseModel):
    tx_id: str
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=4)
    decision: Action
    reasons: str
    timed_out: bool = False


class CrsReport(BaseModel):
    """The CRS checks run on a payment whose combined score fell in the uncertain band."""

    tx_id: str
    score: float  # the Decision's weighted_score that put it in the band
    band: tuple[float, float]  # (low, high), inclusive
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=3)  # prefill, identity, FraudFinder, in order
    decision: Action
    reasons: str
    mocked: bool = True  # False only when the calls went to the real CRS API


class Decision(BaseModel):
    tx_id: str
    action: Action
    weighted_score: float
    votes: list[Vote]
    weights: dict[str, float] = Field(default_factory=dict)
    vetoed_by: str | None = None  # e.g. "mandate:over_cap", "identity:failed"
    escalated: bool = False
    investigator: InvestigatorReport | None = None
    crs: CrsReport | None = None  # set when the score fell in the CRS band
    explanation: str = ""


class SettlementResult(BaseModel):
    tx_id: str
    settled: bool
    tx_hash: str | None = None  # set only for real testnet settlement
    network: str = "eip155:84532"  # Base Sepolia
    reason: str = ""  # why it didn't settle, e.g. "decision was block", "facilitator rejected", "out of test USDC"


# --- Attackers and the arena -------------------------------------------------------


class ShelfKey(BaseModel):
    style: AttackStyle
    amount: AmountBand
    side: Side

    def id(self) -> str:
        return f"{self.style}/{self.amount}/{self.side}"


class Playbook(BaseModel):
    """LLM picks the strategy; the optimizer tunes `params` within `param_bounds`."""

    playbook_id: str
    attacker_id: str
    strategy: str
    params: dict[str, float] = Field(default_factory=dict)
    param_bounds: dict[str, tuple[float, float]] = Field(default_factory=dict)
    shelf: ShelfKey
    free_text_payloads: list[str] = Field(default_factory=list)  # memos, page text


class AttackerRecord(BaseModel):
    attacker_id: str
    name: str
    source: AttackerSource
    model: str | None = None  # Bitdeer model id, or None for hand-written
    parent: str | None = None  # Lane value or "orchestrator" for spawned attackers
    lineage: str | None = None  # outsider persona name, kept across the run
    generation: int = 0
    playbook: Playbook
    profit_cents: int = 0  # attacker profit against the current defense
    scored_round: int | None = None

    @property
    def held_out(self) -> bool:
        return self.source == AttackerSource.HELD_OUT


class AttackFeedback(BaseModel):
    """What an attacker learns about one of its transactions."""

    tx_id: str
    outcome: Action
    objected_by: list[Lane] | None = None  # insiders only; None for outsiders
    settled: bool | None = None  # whether the money moved (a stepped-up payment can go either way)


class ShelfState(BaseModel):
    key: ShelfKey
    attacker_ids: list[str] = Field(default_factory=list, max_length=3)
    outsider_slot: str | None = None  # the reserved outsider spot
    attempts: int = 0
    caught: int = 0
    unknown: bool = True  # fewer than 20 recent attempts

    @property
    def catch_rate(self) -> float:
        return (self.caught + 1) / (self.attempts + 2)

    @property
    def learnability(self) -> float:
        return self.catch_rate * (1 - self.catch_rate)


# --- Threat intel and page scans ---------------------------------------------------


class ThreatReport(BaseModel):
    report_id: str
    source_url: str
    title: str
    text: str  # untrusted; only ever passed to models as quoted data
    published: datetime | None = None
    fetched: datetime


class ThreatPattern(BaseModel):
    report_id: str
    name: str
    description: str
    signals: list[str]  # compositions of existing feature primitives
    lane: Lane
    shelf: ShelfKey


class ThreatIntelItem(BaseModel):
    report: ThreatReport
    pattern: ThreatPattern | None = None
    watch_rule_id: str | None = None
    attacker_ids: list[str] = Field(default_factory=list)
    catches: int = 0


class PageScan(BaseModel):
    url: str
    merchant_id: str | None = None
    injection_score: float = Field(ge=0.0, le=1.0)
    findings: list[str] = Field(default_factory=list)
    listed_price_cents: int | None = None
    scanned_at: datetime


# --- Metrics ------------------------------------------------------------------------


class RoundMetrics(BaseModel):
    run_id: str
    seed: int
    round: int
    defense: str = "full"  # "full", a baseline name, or an ablation name
    loss_merchant_side_cents: int = 0
    loss_buyer_side_cents: int = 0
    false_positive_rate: float = 0.0
    escalation_rate: float = 0.0
    uncovered_misses: int = 0
    exploitability_cents: int = 0
    hijack_rate: float = 0.0
    shelves_defended: float = 0.0
    archive_coverage: float = 0.0
    total_attacker_profit_cents: int = 0
    first_contact_catch_rate: float | None = None
    critic_agreement: float | None = None
    llm_spend_usd: float = 0.0
    frozen: bool = False
    total_cost_cents: int = 0  # missed fraud + false blocks + step-up friction and abandonment (spec 5.1)

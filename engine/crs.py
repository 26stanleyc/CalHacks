"""CRS escalation for uncertain payments (spec 11, CRS; added after M0).

A payment whose combined score (`Decision.weighted_score`) falls inside the uncertain band
[CRS_BAND_LOW, CRS_BAND_HIGH] gets three CRS checks, in this order:

1. Data Prefill, only when there's too little data on the principal (a thin or unknown identity
   profile, or under a day of history): pull what CRS has on them.
2. Identity Verification on that data (or on the profile we already hold).
3. FraudFinder, CRS's email-centric fraud score, when identity verification didn't settle it. A
   passed identity check only says the person exists, not that they authorized this payment.

How the results fold into the decision:
| CRS result                                 | Action  |
|--------------------------------------------|---------|
| identity verification fails                | block   |
| FraudFinder risk is high                   | block   |
| identity passes and FraudFinder risk is low| allow   |
| anything else (review, medium risk, ...)   | step_up |
The investigator's guards apply too: never allow a payment whose memo reads as an injection, and
a code block can only be relaxed to a step-up. Hard vetoes never reach CRS.

CRS runs before the investigator. A payment CRS allowed or blocked isn't escalated; one it left at
step-up can still go to the investigator, which sees the CRS results in its case.

MOCKED: there are no CRS sandbox credentials (evidence/m0/crs-decision.md) and no public endpoint
reference, so `MockCrs` stands in for the API behind the `CrsClient` interface. It plays a credit
bureau: it has files on the world's real people (everyone who exists when the engine starts) and
not on accounts attackers make later. Its answers are deterministic per principal and noisy: a
real thin-file shopper sometimes lands in review, a bought identity sometimes passes, and a
hijacked real person always verifies.
"""

import hashlib
import os
from dataclasses import dataclass, field
from typing import Protocol

from contracts.schemas import Action, CrsReport, Decision, FeatureVector, IdentityStatus, ToolCall, Transaction

INJECTION_GUARD = 0.6  # same as the investigator's
THIN_HISTORY_S = 86_400  # under a day of history counts as too little data
MAX_PER_ROUND = 20  # spec 11: live rounds make at most about 10-20 real CRS calls per round


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    return float(value) if value else default


class CrsClient(Protocol):
    """The three CRS products used here. A real client (once there's sandbox access) implements this.
    Identity data only, never a credit pull (spec 11), and sandbox credentials from .env only."""

    mocked: bool

    def data_prefill(self, principal_id: str) -> dict: ...

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict: ...

    def fraud_finder(self, principal_id: str) -> dict: ...


def _unit(*parts: str) -> float:
    return int(hashlib.sha256(":".join(parts).encode()).hexdigest()[:12], 16) / 16 ** 12


@dataclass
class MockCrs:
    """A simulated credit bureau with the CRS response shapes (see the module docstring)."""

    identities: dict[str, IdentityStatus]  # the world's identity profiles; grows as attackers make accounts
    on_file: set[str]  # the real people the bureau has files on
    mocked: bool = True

    def _kind(self, principal_id: str) -> str:
        identity = self.identities.get(principal_id)
        if identity in (IdentityStatus.MISMATCH, IdentityStatus.FLAGGED):
            return "bad"
        real = principal_id in self.on_file
        if identity == IdentityStatus.VERIFIED:
            return "real_full" if real else "bought_full"  # a bought "verified" identity is a stolen real one
        return "real_thin" if real else "synthetic"

    def data_prefill(self, principal_id: str) -> dict:
        kind, u = self._kind(principal_id), _unit(principal_id, "prefill")
        if kind == "synthetic" and u < 0.6 or kind == "real_thin" and u < 0.1:
            return {"status": "no_record"}
        thin = kind in ("real_thin", "synthetic")
        fields = ["name", "address"] + ([] if thin else ["dob", "phone", "ssn_last4"])
        years = round((0.2 + 1.8 * u) if thin else (3 + 27 * u), 1)
        return {"status": "found", "fields": fields, "record_age_years": years}

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict:
        kind, u = self._kind(principal_id), _unit(principal_id, "idv")
        if prefill is not None and prefill.get("status") == "no_record":
            return {"result": "review", "score": 40, "reason": "no bureau record to match against"}
        if kind == "bad" or kind == "synthetic" and u < 0.55:
            return {"result": "fail", "score": int(5 + 25 * u), "reason": "name, date of birth and SSN don't match one person"}
        if kind == "real_full" and u < 0.95 or kind == "real_thin" and u < 0.8 or kind == "bought_full" and u < 0.85:
            return {"result": "pass", "score": int(80 + 20 * u), "reason": "identity elements match a bureau file"}
        return {"result": "review", "score": int(45 + 30 * u), "reason": "partial match; some elements unverified"}

    def fraud_finder(self, principal_id: str) -> dict:
        kind, u = self._kind(principal_id), _unit(principal_id, "fraudfinder")
        if kind in ("real_full", "real_thin"):
            score = int(5 + 30 * u) if u < 0.92 else int(45 + 25 * u)
        else:
            score = int(75 + 25 * u) if u < 0.6 else int(40 + 30 * u) if u < 0.9 else int(10 + 25 * u)
        level = "high" if score >= 75 else "medium" if score >= 40 else "low"
        return {"risk_score": score, "risk_level": level, "email_age_days": int(3650 * (1 - score / 100) * u) + 1,
                "disposable_domain": score >= 75 and u > 0.5}


@dataclass
class CrsEscalation:
    """Runs the three CRS checks on in-band payments and folds them into the decision."""

    client: CrsClient
    low: float = field(default_factory=lambda: _env_float("CRS_BAND_LOW", 0.30))
    high: float = field(default_factory=lambda: _env_float("CRS_BAND_HIGH", 0.70))
    max_per_round: int = field(default_factory=lambda: int(_env_float("CRS_MAX_PER_ROUND", MAX_PER_ROUND)))

    def __post_init__(self):
        if not 0.0 <= self.low <= self.high <= 1.0:
            raise ValueError(f"CRS band must satisfy 0 <= low <= high <= 1, got ({self.low}, {self.high})")

    def in_band(self, decision: Decision) -> bool:
        return decision.vetoed_by is None and self.low <= decision.weighted_score <= self.high

    def check(self, tx: Transaction, features: FeatureVector, decision: Decision) -> Decision:
        """The decision after the CRS checks (call only for in-band payments)."""
        v = features.values
        calls: list[ToolCall] = []
        prefill = None
        thin = v.get("i_identity_status", 1.0) >= 1.0 or v.get("i_principal_age_s", 0.0) < THIN_HISTORY_S
        if thin:
            prefill = self.client.data_prefill(tx.principal_id)
            calls.append(_call("crs_data_prefill", tx, prefill))
        idv = self.client.identity_verification(tx.principal_id, prefill)
        calls.append(_call("crs_identity_verification", tx, idv))
        risk = None
        if idv["result"] != "fail":
            risk = self.client.fraud_finder(tx.principal_id)
            calls.append(_call("crs_fraud_finder", tx, risk))

        if idv["result"] == "fail":
            action, reasons = Action.BLOCK, f"identity verification failed ({idv['reason']})"
        elif risk["risk_level"] == "high":
            action, reasons = Action.BLOCK, f"FraudFinder risk is high ({risk['risk_score']})"
        elif idv["result"] == "pass" and risk["risk_level"] == "low":
            action, reasons = Action.ALLOW, f"identity verified and FraudFinder risk is low ({risk['risk_score']})"
        else:
            action, reasons = Action.STEP_UP, (f"identity verification: {idv['result']}, "
                                               f"FraudFinder risk {risk['risk_level']} ({risk['risk_score']})")
        if prefill is not None and prefill.get("status") == "no_record":
            reasons = f"CRS has no record of this person; {reasons}"
        if action == Action.ALLOW and v.get("b_memo_injection", 0.0) >= INJECTION_GUARD:
            action = Action.STEP_UP if decision.action == Action.ALLOW else decision.action
            reasons += " (not allowed: the memo reads as an injection)"
        if action == Action.ALLOW and decision.action == Action.BLOCK:
            action = Action.STEP_UP
            reasons += " (a code block can only be relaxed to a step-up)"
        report = CrsReport(tx_id=tx.tx_id, score=decision.weighted_score, band=(self.low, self.high), tool_calls=calls,
                           decision=action, reasons=reasons, mocked=self.client.mocked)
        tag = "CRS check (mocked)" if self.client.mocked else "CRS check"
        return decision.model_copy(update={"action": action, "crs": report,
                                           "explanation": f"{tag}: {reasons[:1].upper()}{reasons[1:]}."})


def _call(tool: str, tx: Transaction, output: dict) -> ToolCall:
    return ToolCall(tool=tool, input={"principal_id": tx.principal_id}, output_summary=str(output)[:300])


def from_env() -> bool:
    """CRS escalation is off unless CRS_ESCALATION is set: the mock's answers shouldn't leak into
    benchmark numbers by default."""
    return os.environ.get("CRS_ESCALATION", "").strip().lower() in {"1", "true", "yes", "on"}

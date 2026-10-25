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

Two clients behind the `CrsClient` interface, picked by CRS_ESCALATION (see `crs_from_env`):
- `HttpCrs`, the real CRS sandbox (https://api-sandbox.stitchcredit.com/api): AccurateAppend reverse
  lookup as the prefill, TransUnion TruValidate identity verification, and AtData FraudFinder. The
  sandbox only answers for CRS's 20 test persons, so each simulated principal is played by one of
  them, picked by what a bureau would know about that principal (`PERSONA_GROUPS`).
- `MockCrs`, a simulated bureau for tests, benchmarks and offline runs.
Both see the principal the same way: the bureau has files on the world's real people (everyone who
exists when the engine starts) and not on accounts attackers make later. A hijacked real person
always verifies: CRS confirms a person exists, not that they authorized the payment.

If CRS can't be reached or refuses (a failed login is never retried: repeated failures lock the
account), the payment keeps its code decision, stepping up instead of allowing, like the investigator.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

from contracts.schemas import Action, CrsReport, Decision, FeatureVector, IdentityStatus, ToolCall, Transaction

INJECTION_GUARD = 0.6  # same as the investigator's
THIN_HISTORY_S = 86_400  # under a day of history counts as too little data
MAX_PER_ROUND = 20  # spec 11: live rounds make at most about 10-20 real CRS calls per round


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    return float(value) if value else default


class CrsClient(Protocol):
    """The three CRS products used here. Identity data only, never a credit pull (spec 11), and
    sandbox credentials from .env only."""

    mocked: bool

    def data_prefill(self, principal_id: str) -> dict: ...

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict: ...

    def fraud_finder(self, principal_id: str) -> dict: ...


def _unit(*parts: str) -> float:
    return int(hashlib.sha256(":".join(parts).encode()).hexdigest()[:12], 16) / 16 ** 12


def bureau_kind(identities: dict[str, IdentityStatus], on_file: set[str], principal_id: str) -> str:
    """What a credit bureau would know about a principal."""
    identity = identities.get(principal_id)
    if identity in (IdentityStatus.MISMATCH, IdentityStatus.FLAGGED):
        return "bad"
    real = principal_id in on_file
    if identity == IdentityStatus.VERIFIED:
        return "real_full" if real else "bought_full"  # a bought "verified" identity is a stolen real one
    return "real_thin" if real else "synthetic"


def risk_level(score: int) -> str:
    return "high" if score >= 75 else "medium" if score >= 40 else "low"  # AtData suggests 70-80 for high


@dataclass
class MockCrs:
    """A simulated credit bureau with the CRS response shapes (see the module docstring)."""

    identities: dict[str, IdentityStatus]  # the world's identity profiles; grows as attackers make accounts
    on_file: set[str]  # the real people the bureau has files on
    mocked: bool = True

    def _kind(self, principal_id: str) -> str:
        return bureau_kind(self.identities, self.on_file, principal_id)

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
        return {"risk_score": score, "risk_level": risk_level(score), "email_age_days": int(3650 * (1 - score / 100) * u) + 1,
                "disposable_domain": score >= 75 and u > 0.5}


PERSONAS: dict[str, dict] = json.loads(Path(__file__).with_name("crs_personas.json").read_text())["personas"]
# Which of CRS's sandbox test persons plays a simulated principal, by what a bureau would know about it.
PERSONA_GROUPS = {
    "real_full": ("P01", "P02", "P10", "P19"),  # clean identities that verify
    "real_thin": ("P03", "P04", "P20"),  # thin file, credit invisible (no prefill record), stale file
    "synthetic": ("P12", "P13"),  # identity mismatch, synthetic identity
    "bought_full": ("P14", "P16"),  # a stolen real identity: deceased SSN, fraud alert on file
    "bad": ("P12", "P14"),
}
FAIL_CODES = {"2201", "2202", "2301"}  # TruValidate: SSN issued to another name, SSN issued before the DOB, deceased SSN


class CrsUnavailable(RuntimeError):
    """CRS refused or couldn't be reached."""


def parse_prefill(body: dict) -> dict:
    records = (body.get("data") or {}).get("records") or []
    if not records:
        return {"status": "no_record"}
    return {"status": "found", "fields": sorted(k for k, v in records[0].items() if v), "records": len(records)}


def parse_identity(body: dict) -> dict:
    """CRS's guide: no messages in addOnProducts[0].idManagerVerification means verified; each message is a problem."""
    tu = ((body.get("addOnProducts") or [{}])[0] or {}).get("idManagerVerification") or {}
    messages = tu.get("messages") or []
    codes = sorted({str(m.get("code")) for m in messages})
    text = "; ".join(m.get("text", "") for m in messages)[:160].lower()
    if tu.get("searchStatus") not in (None, "HIT"):
        return {"result": "review", "codes": codes, "reason": "TransUnion has no file on this person"}
    if not messages:
        return {"result": "pass", "codes": [], "reason": "name, SSN, date of birth and address verify"}
    if FAIL_CODES & set(codes):
        return {"result": "fail", "codes": codes, "reason": text}
    return {"result": "review", "codes": codes, "reason": text}  # e.g. an address mismatch or a fraud alert


def parse_fraud_finder(body: dict) -> dict:
    risk = body.get("risk") or {}
    if "score" not in risk:
        raise CrsUnavailable("FraudFinder returned no risk score")
    score = int(risk["score"])
    return {"risk_score": score, "risk_level": risk_level(score),
            "email_status": (body.get("email_validation") or {}).get("status"),
            "phone_line": (risk.get("phone") or {}).get("line_type")}


def _json(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@dataclass
class HttpCrs:
    """The real CRS sandbox. Logs in once and reuses the token, and never retries a refused login.
    Persons are sent exactly as CRS gives them, so every answer is a free canned sandbox response."""

    base_url: str
    username: str
    password: str = field(repr=False)
    identities: dict[str, IdentityStatus] = field(default_factory=dict)
    on_file: set[str] = field(default_factory=set)
    http: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0))
    mocked: bool = False

    def __post_init__(self):
        if not self.base_url.startswith("https://api-sandbox."):
            raise ValueError("HttpCrs only talks to the CRS sandbox")
        self.base_url = self.base_url.rstrip("/")
        self._token: str | None = None
        self._refused: str | None = None
        self._answers: dict[tuple[str, str], dict] = {}  # the sandbox answers a person the same way every time

    def persona(self, principal_id: str) -> str:
        group = PERSONA_GROUPS[bureau_kind(self.identities, self.on_file, principal_id)]
        return group[int(_unit(principal_id, "persona") * len(group))]

    def _login(self) -> str:
        if self._token is None:
            if self._refused:
                raise CrsUnavailable(self._refused)
            r = self.http.post(f"{self.base_url}/users/login", json={"username": self.username, "password": self.password})
            token = _json(r).get("token") if r.status_code == 200 else None
            if not token:
                self._refused = f"login refused (HTTP {r.status_code} {_json(r).get('codes', '')}), not retrying"
                raise CrsUnavailable(self._refused)
            self._token = token
        return self._token

    def _ask(self, product: str, path: str, principal_id: str) -> tuple[str, dict]:
        persona = self.persona(principal_id)
        if (persona, product) not in self._answers:
            r = self.http.post(self.base_url + path, json=PERSONAS[persona][product],
                               headers={"Authorization": f"Bearer {self._login()}"})
            if r.status_code != 200:
                raise CrsUnavailable(f"{product}: HTTP {r.status_code} {_json(r).get('codes', '')}")
            self._answers[(persona, product)] = _json(r)
        return persona, self._answers[(persona, product)]

    def data_prefill(self, principal_id: str) -> dict:
        persona, body = self._ask("data_prefill", "/accurate-append/reverse", principal_id)
        return {"persona": persona} | parse_prefill(body)

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict:
        persona, body = self._ask("identity_verification", "/transunion/identity-manager-verification/basic", principal_id)
        return {"persona": persona} | parse_identity(body)

    def fraud_finder(self, principal_id: str) -> dict:
        persona, body = self._ask("fraud_finder", "/fraud-finder/fraud-finder", principal_id)
        return {"persona": persona} | parse_fraud_finder(body)


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
        risk = None
        try:
            if thin:
                prefill = self.client.data_prefill(tx.principal_id)
                calls.append(_call("crs_data_prefill", tx, prefill))
            idv = self.client.identity_verification(tx.principal_id, prefill)
            calls.append(_call("crs_identity_verification", tx, idv))
            if idv["result"] != "fail":
                risk = self.client.fraud_finder(tx.principal_id)
                calls.append(_call("crs_fraud_finder", tx, risk))
        except (CrsUnavailable, httpx.HTTPError) as exc:  # an unreachable CRS never stops a round
            action = Action.STEP_UP if decision.action == Action.ALLOW else decision.action
            return self._report(tx, decision, calls, action, f"CRS unavailable ({str(exc)[:120]}); kept the code decision")

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
        return self._report(tx, decision, calls, action, reasons)

    def _report(self, tx: Transaction, decision: Decision, calls: list[ToolCall], action: Action, reasons: str) -> Decision:
        report = CrsReport(tx_id=tx.tx_id, score=decision.weighted_score, band=(self.low, self.high), tool_calls=calls,
                           decision=action, reasons=reasons, mocked=self.client.mocked)
        tag = "CRS check (mocked)" if self.client.mocked else "CRS check"
        return decision.model_copy(update={"action": action, "crs": report,
                                           "explanation": f"{tag}: {reasons[:1].upper()}{reasons[1:]}."})


def _call(tool: str, tx: Transaction, output: dict) -> ToolCall:
    return ToolCall(tool=tool, input={"principal_id": tx.principal_id}, output_summary=str(output)[:300])


def crs_from_env(identities: dict[str, IdentityStatus], on_file: set[str]) -> CrsEscalation | None:
    """CRS_ESCALATION picks the client: off by default (the mock's answers shouldn't leak into
    benchmark numbers), "1" or "mock" for MockCrs, "live" for the real sandbox with CRS_BASE_URL,
    CRS_USERNAME and CRS_PASSWORD from .env."""
    mode = os.environ.get("CRS_ESCALATION", "").strip().lower()
    if mode in ("", "0", "off", "false", "no"):
        return None
    if mode != "live":
        return CrsEscalation(MockCrs(identities, on_file))
    missing = [k for k in ("CRS_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise ValueError(f"CRS_ESCALATION=live needs {', '.join(missing)} in .env")
    return CrsEscalation(HttpCrs(os.environ["CRS_BASE_URL"], os.environ["CRS_USERNAME"], os.environ["CRS_PASSWORD"],
                                 identities, on_file))

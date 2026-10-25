"""CRS escalation for uncertain payments (spec 11, CRS; added after M0).

A payment whose combined score (`Decision.weighted_score`) falls inside the uncertain band
[CRS_BAND_LOW, CRS_BAND_HIGH] gets up to three CRS checks, in this order:

1. Data Append (AccurateAppend), to fill in what the payer didn't send or what we don't know:
   - Reverse (phone or email in, name and address out; `data_prefill`): when the payer hid their
     name and address, or there's too little data on them (a thin or unknown identity profile, or
     under a day of history).
   - Forward (name and address in, phone and email out; `data_append`): when the payer sent no
     phone and no email, and only if the score is at least CRS_APPEND_MIN_SCORE (default 0.5).
2. Identity Verification, when there's a name and address to verify (sent or appended).
3. FraudFinder, CRS's email-centric fraud score, when there's an email (sent or appended) and
   identity verification didn't fail. A passed identity check only says the person exists, not that
   they authorized this payment.

Honest shoppers send all their details. Some attacker-made accounts hide them (`hidden_details`):
throwaway accounts often send no phone or email, or no name and address; bought identities sometimes
send no contact details. That tests whether CRS still works on partial data.

What else of CRS's answers is used, and how:
- Reverse append also says whose phone or email it is. If it belongs to someone other than the
  payer (CRS's P12 test person comes back as a different name), the payment isn't allowed.
- FraudFinder's details behind its score (a days-old email, a prepaid or VoIP phone, a phone or
  address registered to someone else, a proxy IP far from the address, ...) are named in the
  reasons. They explain the score; they don't count a second time in the decision.

How the results fold into the decision:
| CRS result                                       | Action  |
|--------------------------------------------------|---------|
| identity verification fails                      | block   |
| FraudFinder risk is high                         | block   |
| identity passes, FraudFinder low, and the phone  | allow   |
| or email (if looked up) is the payer's           |         |
| anything else (review, medium risk, a check that | step_up |
| couldn't run for missing details, ...)           |         |
The investigator's guards apply too: never allow a payment whose memo reads as an injection, and
a code block can only be relaxed to a step-up. Hard vetoes never reach CRS.

CRS runs before the investigator. A payment CRS allowed or blocked isn't escalated; one it left at
step-up can still go to the investigator, which sees the CRS results in its case.

Watch list: a person FraudFinder rates high risk goes on the CRS watch list. Every later payment of
theirs, whatever its score, is stepped up at least (never allowed), with no new CRS call and no
investigator. The engine logs each addition as a defender action (actor "crs", action "watch"), so a
restarted run picks the list back up.

Two clients behind the `CrsClient` interface, picked by CRS_ESCALATION (see `crs_from_env`):
- `HttpCrs`, the real CRS sandbox (https://api-sandbox.stitchcredit.com/api): AccurateAppend reverse
  lookup, TransUnion TruValidate identity verification, and AtData FraudFinder. The sandbox only
  answers for CRS's 20 test persons, so each simulated principal is played by one of them, picked by
  what a bureau would know about that principal (`PERSONA_GROUPS`). Once Data Append has filled in a
  hidden detail, the person's full canned request is sent (a partial one wouldn't match a test
  person and would go to the live vendor). Forward append has no sandbox data, so `HttpCrs` answers
  it locally from the test person and never sends it.
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
import re
from datetime import date
from collections.abc import Callable
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
    """The CRS products used here, all from the sandbox with credentials from .env.

    The first four are identity data and are what decides a payment. `credit_report` is a
    prequalification (soft) pull and is used for one thing only: the trust line that sets how much a
    newly enrolled agent may spend (`engine/trust_line.py`). It never scores a payment, because a
    good credit file says nothing about whether an agent was hijacked."""

    mocked: bool

    def data_prefill(self, principal_id: str) -> dict: ...  # reverse append: phone or email in, name and address out

    def data_append(self, principal_id: str) -> dict: ...  # forward append: name and address in, phone and email out

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict: ...

    def fraud_finder(self, principal_id: str) -> dict: ...

    def credit_report(self, principal_id: str) -> dict: ...  # all three bureaus, merged


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


def hidden_details(identities: dict[str, IdentityStatus], on_file: set[str], principal_id: str) -> tuple[str, ...]:
    """What a payer leaves out of the identity details sent with a payment. Honest shoppers send
    everything; some attacker-made accounts hide their contact details, or their name and address."""
    kind, u = bureau_kind(identities, on_file, principal_id), _unit(principal_id, "hidden")
    if kind == "synthetic":  # throwaway accounts
        return ("phone", "email") if u < 0.35 else ("name_address",) if u < 0.55 else ()
    if kind == "bought_full":  # a stolen identity, without the victim's contact details
        return ("phone", "email") if u < 0.5 else ()
    return ()


def risk_level(score: int) -> str:
    return "high" if score >= 75 else "medium" if score >= 40 else "low"  # AtData suggests 70-80 for high


FARM_CONTACT_POOL = 6  # how few phones and inboxes a bulk-made batch of accounts shares


BUREAUS = ("transunion", "equifax", "experian")
# `creditFiles[].creditFileDetail` values, plus Deceased for the deceased errorMessages the bureaus
# return instead of a file.
FILE_RETURNED, NO_HIT, FROZEN, DECEASED = "FileReturned", "NoFileReturnedNoHit", "NoFileReturnedCreditFreeze", "Deceased"
CREDIT_CONFIGS = {"transunion": "tu-prequal-fico9", "equifax": "efx-prequal-vantage4",
                  "experian": "exp-prequal-vantage4"}
CREDIT_PATHS = {b: f"/{b}/credit-report/standard/{CREDIT_CONFIGS[b]}" for b in BUREAUS}


def merge_credit_reports(per_bureau: dict[str, dict]) -> dict:
    """Merge the three bureaus into one answer. They disagree on purpose in CRS's test data (P15 is
    frozen at TransUnion only, P17 has a file at two of three), so the merge takes the median of
    the scores that came back and counts how many bureaus actually returned a file."""
    scored = sorted(b["score"] for b in per_bureau.values() if b.get("score"))
    files = [b for b in per_bureau.values() if b.get("status") == FILE_RETURNED]
    deceased = any(b.get("status") == DECEASED for b in per_bureau.values())
    return {"score": scored[len(scored) // 2] if scored else 0,
            "bureaus_with_file": len(files), "bureaus_scored": len(scored),
            "deceased": deceased, "frozen": any(b.get("status") == FROZEN for b in per_bureau.values()),
            "no_file": not files and not deceased, "per_bureau": per_bureau}


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
        return {"status": "found", "fields": fields, "record_age_years": years,
                "matches_payer": not (kind == "synthetic" and u > 0.8)}  # a borrowed or recycled number

    def data_append(self, principal_id: str) -> dict:
        kind, u = self._kind(principal_id), _unit(principal_id, "append")
        if u >= {"real_full": 0.95, "real_thin": 0.85, "bought_full": 0.8}.get(kind, 0.35):  # throwaways rarely link up
            return {"status": "no_match", "found": [], "contact": ""}
        # Whose phone and email these are. A real person has their own; accounts made in bulk reuse a
        # small pool of numbers and inboxes, which is what makes a farm visible from outside.
        pool = FARM_CONTACT_POOL if kind in ("synthetic", "bad") else 0
        contact = f"c-{int(_unit(principal_id, 'contact') * pool)}" if pool else f"c-{principal_id}"
        return {"status": "found", "found": ["email", "phone"], "contact": contact}

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
        email_days = int(3650 * (1 - score / 100) * u) + 1
        signals = ([f"email first seen {email_days} days ago"] if email_days < 90 else []) + (
            ["disposable email domain"] if score >= 75 and u > 0.5 else []) + (
            ["prepaid VoIP phone"] if score >= 75 and u < 0.5 else []) + (
            ["phone registered to someone else"] if score >= 60 and u > 0.3 else [])
        return {"risk_score": score, "risk_level": risk_level(score), "signals": signals}

    def credit_report(self, principal_id: str) -> dict:
        """A soft pull at all three bureaus, in the shape `merge_credit_reports` returns. Only the
        trust line reads this, and only when an agent enrolls."""
        kind, u = self._kind(principal_id), _unit(principal_id, "credit")
        if kind == "bad":
            per = {b: {"status": DECEASED if u < 0.5 else NO_HIT, "score": 0} for b in BUREAUS}
        elif kind == "synthetic":
            per = {b: {"status": NO_HIT, "score": 0} for b in BUREAUS}
        elif kind == "bought_full":  # a real file behind a stolen identity: good, sometimes frozen
            score = int(690 + 110 * u)
            per = {b: {"status": FILE_RETURNED, "score": score} for b in BUREAUS}
            if u > 0.7:
                per["transunion"] = {"status": FROZEN, "score": 0}
        elif kind == "real_thin":
            per = ({b: {"status": NO_HIT, "score": 0} for b in BUREAUS} if u < 0.3
                   else {b: {"status": FILE_RETURNED, "score": int(620 + 70 * u)} for b in BUREAUS})
        else:  # real_full
            per = {b: {"status": FILE_RETURNED, "score": int(680 + 135 * u)} for b in BUREAUS}
        return merge_credit_reports(per)


PERSONAS: dict[str, dict] = json.loads(Path(__file__).with_name("crs_personas.json").read_text())["personas"]


@dataclass
class PersonaCrs:
    """A bureau that plays one of CRS's 20 sandbox test persons exactly, from the outcomes CRS
    publishes for them (`crs_personas.json`). `MockCrs` invents a plausible person from an identity
    profile, which is right for a simulated marketplace but cannot reproduce a credit freeze, a
    fraud alert or two bureaus disagreeing. This can, so the conformance suite means something
    without credentials and offline runs behave like the live sandbox."""

    persona_id: str
    mocked: bool = True

    @property
    def person(self) -> dict:
        return PERSONAS[self.persona_id]

    def data_prefill(self, principal_id: str) -> dict:
        if self.persona_id in NO_CONTACT_RECORD:
            return {"status": "no_record"}
        thin = self.person["decision"] == "NO_FILE" or "thin" in self.person["profile"].lower()
        return {"status": "found", "fields": ["name", "address"] + ([] if thin else ["dob", "phone", "ssn_last4"]),
                "record_age_years": 1.0 if thin else 12.0,
                "matches_payer": self.person["identity_expected"] != "fail"}

    def data_append(self, principal_id: str) -> dict:
        contact = self.person["contact"]["phone"]
        if self.persona_id in NO_CONTACT_RECORD or not contact:
            return {"status": "no_match", "found": [], "contact": ""}
        return {"status": "found", "found": ["email", "phone"], "contact": contact}

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict:
        verdict = self.person["identity_expected"]
        return {"persona": self.persona_id, "result": verdict, "score": {"pass": 90, "review": 55, "fail": 15}[verdict],
                "reason": self.person["identity_verification_expected"]}

    def fraud_finder(self, principal_id: str) -> dict:
        score = int(self.person["fraud_score_expected"])
        text = self.person["fraud_finder_expected"]
        signals = [s for s in ("email 3 days old", "email invalid", "phone dead", "voip prepaid",
                               "phone owner no_match") if s in text]
        return {"persona": self.persona_id, "risk_score": score, "risk_level": risk_level(score),
                "signals": signals}

    def credit_report(self, principal_id: str) -> dict:
        return {"persona": self.persona_id} | merge_credit_reports(
            {b: dict(v) for b, v in self.person["credit_expected"].items()})
# Which of CRS's sandbox test persons plays a simulated principal, by what a bureau would know about it.
PERSONA_GROUPS = {
    "real_full": ("P01", "P02", "P10", "P19"),  # clean identities that verify
    "real_thin": ("P03", "P04", "P20"),  # thin file, credit invisible (no prefill record), stale file
    "synthetic": ("P12", "P13"),  # identity mismatch, synthetic identity
    "bought_full": ("P14", "P16"),  # a stolen real identity: deceased SSN, fraud alert on file
    "bad": ("P12", "P14"),
}
NO_CONTACT_RECORD = {"P04", "P13"}  # the test persons the reverse lookup has no record of
FAIL_CODES = {"2201", "2202", "2301"}  # TruValidate: SSN issued to another name, SSN issued before the DOB, deceased SSN


class CrsUnavailable(RuntimeError):
    """CRS refused or couldn't be reached."""


def parse_prefill(body: dict, payer: tuple[str, str] | None = None) -> dict:
    """payer: (first, last) name the payer gave, to check the phone or email is theirs."""
    records = (body.get("data") or {}).get("records") or []
    if not records:
        return {"status": "no_record"}
    out = {"status": "found", "fields": sorted(k for k, v in records[0].items() if v), "records": len(records)}
    if payer:
        names = {(str(r.get("firstName", "")).upper(), str(r.get("lastName", "")).upper()) for r in records}
        out["matches_payer"] = (payer[0].upper(), payer[1].upper()) in names
    return out


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


def parse_fraud_finder(body: dict, today: date | None = None) -> dict:
    risk = body.get("risk") or {}
    if "score" not in risk:
        raise CrsUnavailable("FraudFinder returned no risk score")
    score = int(risk["score"])
    return {"risk_score": score, "risk_level": risk_level(score), "signals": fraud_signals(body, today or date.today())}


def fraud_signals(body: dict, today: date) -> list[str]:
    """The red flags behind a FraudFinder score, in words, for the reasons."""
    risk, email = body.get("risk") or {}, body.get("email_validation") or {}
    phone, postal, ip = risk.get("phone") or {}, risk.get("postal") or {}, risk.get("ip") or {}
    out = []
    if email.get("status") in ("invalid", "risky", "unverifiable"):
        out.append(f"email {email['status']}")
    if email.get("domain_type") == "disposable":
        out.append("disposable email domain")
    first_seen = (body.get("eam") or {}).get("date_first_seen")
    if first_seen:
        days = (today - date.fromisoformat(first_seen[:10])).days
        if 0 <= days < 90:
            out.append(f"email first seen {days} days ago")
    line = " ".join(w for w in ("prepaid" if phone.get("prepaid") else "",
                                "VoIP" if phone.get("line_type") == "voip" else "") if w)
    if line:
        out.append(f"{line} phone")
    if phone.get("status") in ("dead", "invalid"):
        out.append(f"phone {phone['status']}")
    if "no_match" in ((phone.get("owner") or {}).get("first_name_match"), (phone.get("owner") or {}).get("last_name_match")):
        out.append("phone registered to someone else")
    if postal.get("last_name_match") == "no_match":
        out.append("address registered to someone else")
    if ip.get("proxy_type") or ip.get("hosting_facility"):
        out.append("IP is a proxy or hosting provider")
    miles = re.search(r"(\d+)", str(postal.get("ip_postal_distance") or ""))
    if miles and int(miles.group(1)) >= 500:
        out.append(f"IP {postal['ip_postal_distance']} from the address")
    return out


def parse_credit_report(body: dict) -> dict:
    """One bureau's answer, from the fields CRS documents. A credit report reports trouble as HTTP
    200 with `errorMessages`, so only the body decides: a missing file, a credit freeze, a deceased
    flag and a wrong config all arrive as a success."""
    errors = [str(e.get("creditErrorMessageText", "")) for e in (body.get("errorMessages") or [])]
    if any("deceased" in e.lower() for e in errors):
        return {"status": DECEASED, "score": 0, "errors": errors}
    files = body.get("creditFiles") or []
    status = str(files[0].get("creditFileDetail") or "") if files else (NO_HIT if not errors else "")
    scores = body.get("scores") or []
    value = next((s.get("scoreValue") for s in scores if s.get("scoreValue") not in (None, "")), None)
    model = next((str(s.get("modelName")) for s in scores if s.get("modelName")), "")
    reason = next((str(s.get("noScoreReasonType")) for s in scores if s.get("noScoreReasonType")), "")
    return {"status": status or NO_HIT, "score": int(value) if value is not None else 0,
            "model": model, "no_score_reason": reason, "errors": errors}


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
    requests: list[dict] = field(default_factory=list)  # every live call, with its RequestID

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

    def _record(self, product: str, persona: str, response: httpx.Response) -> None:
        """Every live call, with the RequestID CRS returns. CRS's `debug_request` takes that id, and
        the FCRA expects a lender to be able to say which bureau calls it made about a person and
        why, so the id is the one field worth keeping (`engine/crs_audit.py`)."""
        self.requests.append({"product": product, "persona": persona,
                              "request_id": response.headers.get("RequestID", ""),
                              "status": response.status_code})

    def _ask(self, product: str, path: str, principal_id: str) -> tuple[str, dict]:
        persona = self.persona(principal_id)
        if (persona, product) not in self._answers:
            r = self.http.post(self.base_url + path, json=PERSONAS[persona][product],
                               headers={"Authorization": f"Bearer {self._login()}"})
            if r.status_code != 200:
                raise CrsUnavailable(f"{product}: HTTP {r.status_code} {_json(r).get('codes', '')}")
            self._record(product, persona, r)
            self._answers[(persona, product)] = _json(r)
        return persona, self._answers[(persona, product)]

    def logs(self) -> list[dict]:
        """CRS's own record of this account's recent requests (`GET /users/logs`), to check ours
        against theirs."""
        r = self.http.get(f"{self.base_url}/users/logs", headers={"Authorization": f"Bearer {self._login()}"})
        if r.status_code != 200:
            raise CrsUnavailable(f"logs: HTTP {r.status_code}")
        body = r.json()
        return body if isinstance(body, list) else body.get("content", [])

    def retention(self, request_id: str) -> dict:
        """What CRS retained for one request (`GET /users/retention/{id}`)."""
        r = self.http.get(f"{self.base_url}/users/retention/{request_id}",
                          headers={"Authorization": f"Bearer {self._login()}"})
        if r.status_code != 200:
            raise CrsUnavailable(f"retention {request_id}: HTTP {r.status_code}")
        return _json(r)

    def data_prefill(self, principal_id: str) -> dict:
        persona, body = self._ask("data_prefill", "/accurate-append/reverse", principal_id)
        given = PERSONAS[persona]["identity_verification"]
        return {"persona": persona} | parse_prefill(body, (given["firstName"], given["lastName"]))

    def data_append(self, principal_id: str) -> dict:
        """Forward append has no sandbox test data (calls would go to the live AccurateAppend service),
        so it's answered here from the test person and never sent."""
        persona = self.persona(principal_id)
        found = [] if persona in NO_CONTACT_RECORD else ["email", "phone"]
        # The test person's own phone and email. Several principals play the same test person, so
        # they share a contact, which is exactly the shape a farm has.
        contact = PERSONAS[persona]["contact"]["phone"] if found else ""
        return {"persona": persona, "status": "found" if found else "no_match", "found": found,
                "contact": contact, "simulated": "no sandbox data for forward append; not sent"}

    def identity_verification(self, principal_id: str, prefill: dict | None) -> dict:
        persona, body = self._ask("identity_verification", "/transunion/identity-manager-verification/basic", principal_id)
        return {"persona": persona} | parse_identity(body)

    def fraud_finder(self, principal_id: str) -> dict:
        persona, body = self._ask("fraud_finder", "/fraud-finder/fraud-finder", principal_id)
        return {"persona": persona} | parse_fraud_finder(body)

    def credit_report(self, principal_id: str) -> dict:
        """One soft pull per bureau, merged. Each bureau takes the same body at the root, so this is
        three calls; the sandbox answers a test person the same way every time, so they are cached
        like every other product."""
        persona = self.persona(principal_id)
        bodies = PERSONAS[persona]["credit_report"]
        per_bureau = {}
        for bureau in BUREAUS:
            product = f"credit_report_{bureau}"
            if (persona, product) not in self._answers:
                r = self.http.post(self.base_url + CREDIT_PATHS[bureau], json=bodies[bureau]["body"],
                                   headers={"Authorization": f"Bearer {self._login()}"})
                if r.status_code != 200:
                    raise CrsUnavailable(f"{product}: HTTP {r.status_code} {_json(r).get('codes', '')}")
                self._record(product, persona, r)
                self._answers[(persona, product)] = _json(r)
            per_bureau[bureau] = parse_credit_report(self._answers[(persona, product)])
        return {"persona": persona} | merge_credit_reports(per_bureau)


@dataclass
class CrsEscalation:
    """Runs the three CRS checks on in-band payments and folds them into the decision."""

    client: CrsClient
    low: float = field(default_factory=lambda: _env_float("CRS_BAND_LOW", 0.30))
    high: float = field(default_factory=lambda: _env_float("CRS_BAND_HIGH", 0.70))
    max_per_round: int = field(default_factory=lambda: int(_env_float("CRS_MAX_PER_ROUND", MAX_PER_ROUND)))
    append_min: float = field(default_factory=lambda: _env_float("CRS_APPEND_MIN_SCORE", 0.50))  # forward append from here
    hidden: Callable[[str], tuple[str, ...]] | None = None  # what a payer didn't send (None: everyone sends everything)
    watched: dict[str, dict] = field(default_factory=dict)  # principal -> the FraudFinder flag that put them there
    new_watches: list[dict] = field(default_factory=list)  # additions the engine hasn't logged yet

    def __post_init__(self):
        if not 0.0 <= self.low <= self.high <= 1.0:
            raise ValueError(f"CRS band must satisfy 0 <= low <= high <= 1, got ({self.low}, {self.high})")

    def in_band(self, decision: Decision) -> bool:
        return decision.vetoed_by is None and self.low <= decision.weighted_score <= self.high

    def is_watched(self, principal_id: str) -> bool:
        return principal_id in self.watched

    def watch(self, tx: Transaction, decision: Decision) -> Decision:
        """A payment from someone on the watch list: stepped up at least, whatever its score."""
        action = Action.BLOCK if decision.action == Action.BLOCK else Action.STEP_UP
        reasons = f"on the CRS watch list since FraudFinder rated them high risk ({self.watched[tx.principal_id]['risk_score']})"
        return self._report(tx, decision, [], action, reasons, watch="on_list")

    def restore(self, actions: list[dict]) -> None:
        """Pick the watch list back up on a restarted run from its logged defender actions."""
        for row in actions:
            if row["actor"] == "crs" and row["action"] == "watch":
                self.watched[row["detail"]["principal_id"]] = row["detail"]

    def check(self, tx: Transaction, features: FeatureVector, decision: Decision) -> Decision:
        """The decision after the CRS checks (call only for in-band payments)."""
        v = features.values
        hidden = list(self.hidden(tx.principal_id)) if self.hidden else []
        has_contact = not {"phone", "email"} <= set(hidden)
        has_email, has_name = "email" not in hidden, "name_address" not in hidden
        thin = v.get("i_identity_status", 1.0) >= 1.0 or v.get("i_principal_age_s", 0.0) < THIN_HISTORY_S
        calls: list[ToolCall] = []
        notes: list[str] = []
        idv = risk = None
        try:
            someone_else = False
            if has_contact and (thin or not has_name):  # 1. Data Append, reverse
                prefill = self.client.data_prefill(tx.principal_id)
                calls.append(_call("crs_data_prefill", tx, prefill))
                someone_else = prefill.get("matches_payer") is False and "name_address" not in hidden
                if someone_else:
                    notes.append("their phone or email belongs to someone else")
                elif prefill.get("status") == "found":
                    has_name = True
                else:
                    notes.append("CRS has no record of this person")
            elif not has_contact:  # 1. Data Append, forward
                if decision.weighted_score >= self.append_min:
                    appended = self.client.data_append(tx.principal_id)
                    calls.append(_call("crs_data_append", tx, appended))
                    has_email = "email" in appended.get("found", [])
                    if not has_email:
                        notes.append("data append found no email for them")
                else:
                    notes.append(f"score under {self.append_min:.2f}, so no data append")
            if has_name:  # 2. Identity Verification
                idv = self.client.identity_verification(tx.principal_id, None)
                calls.append(_call("crs_identity_verification", tx, idv))
            if has_email and (idv is None or idv["result"] != "fail"):  # 3. FraudFinder
                risk = self.client.fraud_finder(tx.principal_id)
                calls.append(_call("crs_fraud_finder", tx, risk))
        except (CrsUnavailable, httpx.HTTPError) as exc:  # an unreachable CRS never stops a round
            action = Action.STEP_UP if decision.action == Action.ALLOW else decision.action
            return self._report(tx, decision, calls, action, f"CRS unavailable ({str(exc)[:120]}); kept the code decision",
                                hidden=hidden)

        if idv and idv["result"] == "fail":
            action, reasons = Action.BLOCK, f"identity verification failed ({idv['reason']})"
        elif risk and risk["risk_level"] == "high":
            action, reasons = Action.BLOCK, f"FraudFinder risk is high ({_risk_text(risk)}); added to the CRS watch list"
            entry = {"principal_id": tx.principal_id, "tx_id": tx.tx_id, "risk_score": risk["risk_score"]}
            self.watched[tx.principal_id] = entry
            self.new_watches.append(entry)
        elif idv and idv["result"] == "pass" and risk and risk["risk_level"] == "low" and not someone_else:
            action, reasons = Action.ALLOW, f"identity verified and FraudFinder risk is low ({_risk_text(risk)})"
        else:
            action = Action.STEP_UP
            reasons = ", ".join([f"identity verification: {idv['result']}" if idv else "identity not verified (no name and address)",
                                 f"FraudFinder risk {risk['risk_level']} ({_risk_text(risk)})" if risk
                                 else "no FraudFinder (no email)"])
        if hidden:
            notes.insert(0, f"payer didn't send their {_details_text(hidden)}")
        if notes:
            reasons = f"{'; '.join(notes)}; {reasons}"
        if action == Action.ALLOW and v.get("b_memo_injection", 0.0) >= INJECTION_GUARD:
            action = Action.STEP_UP if decision.action == Action.ALLOW else decision.action
            reasons += " (not allowed: the memo reads as an injection)"
        if action == Action.ALLOW and decision.action == Action.BLOCK:
            action = Action.STEP_UP
            reasons += " (a code block can only be relaxed to a step-up)"
        return self._report(tx, decision, calls, action, reasons, watch="added" if tx.principal_id in self.watched else None,
                            hidden=hidden)

    def _report(self, tx: Transaction, decision: Decision, calls: list[ToolCall], action: Action, reasons: str,
                watch: str | None = None, hidden: list[str] | None = None) -> Decision:
        report = CrsReport(tx_id=tx.tx_id, score=decision.weighted_score, band=(self.low, self.high), tool_calls=calls,
                           decision=action, reasons=reasons, mocked=self.client.mocked, watch=watch, hidden=hidden or [])
        tag = "CRS check (mocked)" if self.client.mocked else "CRS check"
        return decision.model_copy(update={"action": action, "crs": report,
                                           "explanation": f"{tag}: {reasons[:1].upper()}{reasons[1:]}."})


def _risk_text(risk: dict) -> str:
    signals = risk.get("signals") or []
    return f"{risk['risk_score']}: {', '.join(signals)}" if signals else str(risk["risk_score"])


def _details_text(hidden: list[str]) -> str:
    names = {"name_address": "name and address", "phone": "phone", "email": "email"}
    return " or ".join(names[h] for h in hidden)


def _call(tool: str, tx: Transaction, output: dict) -> ToolCall:
    return ToolCall(tool=tool, input={"principal_id": tx.principal_id}, output_summary=str(output)[:300])


def crs_from_env(identities: dict[str, IdentityStatus], on_file: set[str]) -> CrsEscalation | None:
    """CRS_ESCALATION picks the client: off by default (the mock's answers shouldn't leak into
    benchmark numbers), "1" or "mock" for MockCrs, "live" for the real sandbox with CRS_BASE_URL,
    CRS_USERNAME and CRS_PASSWORD from .env."""
    mode = os.environ.get("CRS_ESCALATION", "").strip().lower()
    if mode in ("", "0", "off", "false", "no"):
        return None
    hidden = lambda principal_id: hidden_details(identities, on_file, principal_id)  # noqa: E731
    if mode != "live":
        return CrsEscalation(MockCrs(identities, on_file), hidden=hidden)
    missing = [k for k in ("CRS_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise ValueError(f"CRS_ESCALATION=live needs {', '.join(missing)} in .env")
    return CrsEscalation(HttpCrs(os.environ["CRS_BASE_URL"], os.environ["CRS_USERNAME"], os.environ["CRS_PASSWORD"],
                                 identities, on_file), hidden=hidden)

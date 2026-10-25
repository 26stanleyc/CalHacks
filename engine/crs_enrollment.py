"""CRS enrollment: who is the human behind this agent, asked once, before the agent ever pays.

The escalation path in `engine/crs.py` asks CRS about a payment that already looks uncertain, which
is a few payments a round. Enrollment asks about the *person* when their agent first appears, so
every payment they ever make is decided with the bureau's answer already in hand. That is what the
identity specialist votes with (`i_crs_*` features), what sets the agent's spending limit
(`engine/trust_line.py`), and what makes accounts made in bulk visible (`g_crs_principals_per_contact`).

One principal costs at most four CRS calls, once, and the answers are cached for the whole run. The
sandbox has 20 test persons and answers each the same way every time, so a run of any size needs at
most 20 x 4 real calls however many agents it has.

Order, stopping as soon as the answer is settled:
1. Reverse append (`data_prefill`): what the bureau has on file for this person.
2. Identity verification: do the name, SSN, date of birth and address belong together.
3. FraudFinder: the email-centric risk score, and the signals behind it.
4. Credit report, all three bureaus, only for the trust line, and only when the identity verified.
   A credit file says nothing about whether an agent was hijacked, so it never scores a payment.

If CRS can't be reached, enrollment returns `UNKNOWN`, which reads as a thin file: no claim either
way, and the other specialists still decide. CRS being down must never allow a payment that would
otherwise be stopped.
"""

import re
from dataclasses import dataclass, field, replace

from engine.crs import CrsClient, CrsUnavailable, risk_level

VERIFIED, REVIEW, FAILED = 0.0, 1.0, 2.0  # i_crs_verified
CLEAN, RISKY, NO_PHONE = 0.0, 1.0, 2.0  # i_crs_phone_risk
UNKNOWN_EMAIL_AGE = 3650.0  # no answer reads as an old, settled inbox: never a reason on its own

_EMAIL_AGE = re.compile(r"email first seen (\d+) days ago")
_RISKY_PHONE = ("voip", "prepaid", "phone dead", "phone invalid", "phone registered to someone else")


@dataclass(frozen=True)
class CrsProfile:
    """What CRS knows about one person. Every field is also a feature or a trust-line input."""

    principal_id: str = ""
    persona: str = ""  # which CRS test person answered, on the live sandbox
    verified: float = REVIEW  # VERIFIED / REVIEW / FAILED
    fraud_score: int = 40  # 0-100, higher is riskier
    file_depth: float = 0.0  # 0 nothing on file .. 1 a long, full file
    email_age_days: float = UNKNOWN_EMAIL_AGE
    phone_risk: float = CLEAN
    contact: str = ""  # the phone or inbox the person resolves to, shared across a farm
    credit_score: int = 0  # 0 = no score (no file, frozen, or not scoreable)
    bureaus_with_file: int = 0
    deceased: bool = False
    reasons: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()  # which CRS products were asked, in order
    available: bool = True  # False when CRS could not be reached

    @property
    def risk_level(self) -> str:
        return risk_level(self.fraud_score)


UNKNOWN = CrsProfile(reasons=("CRS unavailable",), available=False)


def _file_depth(prefill: dict) -> float:
    """How much the bureau holds on this person, as 0..1. A thin or missing file is itself a signal:
    a person with no history is either very young, very new to the country, or invented."""
    if prefill.get("status") != "found":
        return 0.0
    fields = len(prefill.get("fields") or ())
    years = float(prefill.get("record_age_years") or 0.0)
    return round(min(1.0, (min(years, 10.0) / 10.0) * 0.6 + (min(fields, 5) / 5.0) * 0.4), 3)


def _email_age(signals: list[str]) -> float:
    for signal in signals:
        found = _EMAIL_AGE.search(signal)
        if found:
            return float(found.group(1))
    return UNKNOWN_EMAIL_AGE


def _phone_risk(signals: list[str], appended: dict) -> float:
    if any(any(bad in s.lower() for bad in _RISKY_PHONE) for s in signals):
        return RISKY
    return CLEAN if appended.get("found") else NO_PHONE


@dataclass
class CrsEnrollment:
    """Resolves each principal once and remembers the answer for the run."""

    client: CrsClient
    pull_credit: bool = True  # the trust line's soft pull; off for runs that only want identity
    audit: list[dict] = field(default_factory=list)  # one row per product asked, for the FCRA log
    profiles: dict[str, CrsProfile] = field(default_factory=dict)
    contacts: dict[str, set] = field(default_factory=dict)  # contact -> the principals sharing it

    def _ask(self, product: str, principal_id: str, call):
        self.audit.append({"principal_id": principal_id, "product": product,
                           "purpose": "agent enrollment: identity of the human behind the agent",
                           "mocked": getattr(self.client, "mocked", True)})
        return call()

    def profile(self, principal_id: str) -> CrsProfile:
        """This person's CRS profile, asked once and cached."""
        if principal_id not in self.profiles:
            self.profiles[principal_id] = self._resolve(principal_id)
        return self.profiles[principal_id]

    def _resolve(self, principal_id: str) -> CrsProfile:
        try:
            prefill = self._ask("data_prefill", principal_id, lambda: self.client.data_prefill(principal_id))
            identity = self._ask("identity_verification", principal_id,
                                 lambda: self.client.identity_verification(principal_id, prefill))
            fraud = self._ask("fraud_finder", principal_id, lambda: self.client.fraud_finder(principal_id))
            appended = self._ask("data_append", principal_id, lambda: self.client.data_append(principal_id))
        except CrsUnavailable as e:
            return replace(UNKNOWN, principal_id=principal_id, reasons=(f"CRS unavailable: {e}",))

        result = {"pass": VERIFIED, "fail": FAILED}.get(identity.get("result", ""), REVIEW)
        signals = list(fraud.get("signals") or ())
        score = int(fraud.get("risk_score", 40))
        calls = ["data_prefill", "identity_verification", "fraud_finder", "data_append"]

        credit: dict = {}
        if self.pull_credit and result != FAILED:  # no soft pull on an identity that didn't verify
            try:
                credit = self._ask("credit_report", principal_id, lambda: self.client.credit_report(principal_id))
                calls.append("credit_report")
            except CrsUnavailable as e:
                signals.append(f"credit report unavailable: {e}")

        reasons = signals[:]
        if result == FAILED:
            reasons.insert(0, identity.get("reason", "identity verification failed"))
        if prefill.get("status") != "found":
            reasons.append("no bureau record for this person")
        if prefill.get("matches_payer") is False:
            reasons.append("the phone or email belongs to someone else")
        if credit.get("deceased"):
            reasons.append("bureau file flagged deceased")

        contact = str(appended.get("contact") or "")
        if contact:
            self.contacts.setdefault(contact, set()).add(principal_id)

        return CrsProfile(
            principal_id=principal_id,
            persona=str(identity.get("persona") or fraud.get("persona") or ""),
            verified=result,
            fraud_score=score,
            file_depth=_file_depth(prefill),
            email_age_days=_email_age(signals),
            phone_risk=_phone_risk(signals, appended),
            contact=contact,
            credit_score=int(credit.get("score", 0)),
            bureaus_with_file=int(credit.get("bureaus_with_file", 0)),
            deceased=bool(credit.get("deceased")),
            reasons=tuple(reasons),
            calls=tuple(calls),
        )

    def fork(self) -> "CrsEnrollment":
        """A copy for the arena's sandbox: it knows everyone already enrolled, and the throwaway
        accounts the sandbox attackers make resolve against it without touching the live cache, the
        live farm counts or the live audit log."""
        return CrsEnrollment(self.client, self.pull_credit, audit=[], profiles=dict(self.profiles),
                             contacts={c: set(m) for c, m in self.contacts.items()})

    def principals_sharing_contact(self, principal_id: str) -> int:
        """How many enrolled people resolve to the same phone or inbox as this one. One is a normal
        person; several at once is a batch of accounts made by one operator."""
        contact = self.profiles[principal_id].contact if principal_id in self.profiles else ""
        return len(self.contacts.get(contact, ())) if contact else 1

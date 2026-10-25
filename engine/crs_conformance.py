"""The persona conformance suite: do we land every CRS test person where CRS says we should?

CRS ships 20 sandbox test persons, and each one carries the decision it is meant to produce:
APPROVE, REVIEW, DECLINE or NO_FILE. Their own guidance is to "build your decision logic so each
persona lands on its expected decision", so this runs all 20 through the real enrollment path and
prints expected against actual.

What is being checked is the decision our *identity layer* reaches about a person: the trust-line
band that CRS's answer earns them. It is not a payment decision, because a persona is a person and
not a payment; a verified person with a clean file can still have their agent hijacked a minute
later, which is what the specialists are for.

How a band maps to CRS's four outcomes:

| CRS says | we must reach    | meaning                                                      |
|----------|------------------|--------------------------------------------------------------|
| APPROVE  | full             | a verified identity with a scoreable file and low fraud risk  |
| REVIEW   | standard/starter | real enough to trade with, not clean enough for a full line   |
| DECLINE  | restricted       | the bureau cannot stand behind this identity                  |
| NO_FILE  | starter          | nobody on file: not a fraud signal, just nothing to go on     |

Run it against the mock bureau (deterministic, no network):

    uv run python -m engine.crs_conformance

or against the real sandbox, which is the one that counts for the demo:

    CRS_ESCALATION=live uv run python -m engine.crs_conformance --out evidence/crs/conformance.md
"""

import argparse
import os
import sys
from dataclasses import dataclass

from engine.crs import PERSONAS, HttpCrs, PersonaCrs
from engine.crs_enrollment import CrsEnrollment
from engine.trust_line import band_for

# Which trust-line bands satisfy each of CRS's expected decisions.
ACCEPTED: dict[str, tuple[str, ...]] = {
    "APPROVE": ("full",),
    "REVIEW": ("standard", "starter"),
    "DECLINE": ("restricted",),
    "NO_FILE": ("starter",),
}

# CRS's expected decisions are written for a lender or a landlord: "would you extend credit to this
# person, or rent to them". Ours is a fraud gateway deciding what someone's *agent* may spend. On
# most personas those agree. On these five they do not, and we deviate on purpose, because matching
# CRS here would mean refusing payments for reasons that are not fraud. Each one says what CRS's
# decision rests on and why we answer differently.
BY_DESIGN: dict[str, str] = {
    "P05": "declined on creditworthiness (subprime). A thin wallet earns a smaller line, not a "
           "refusal: we are setting a spending limit, not lending money.",
    "P07": "declined on an active Chapter 13. Same reason: it lowers capacity, and capacity is a "
           "limit, not evidence that a payment is fraud.",
    "P11": "declined on eviction history, which is a landlord's question. It says nothing about "
           "whether this person's agent was hijacked.",
    "P09": "declined on a felony conviction with clean credit and a verified identity. We do not "
           "run criminal checks on buyers: a conviction is not evidence that a payment is fraud, "
           "and screening payers on one would be both wrong and useless here.",
    "P08": "a tax lien and a civil judgment, which live in PitchPoint bankruptcies/liens/judgments. "
           "We do not call that product: it speaks to whether someone pays their debts, not to "
           "whether this payment is theirs.",
}


@dataclass
class Row:
    persona: str
    name: str
    profile: str
    expected: str
    band: str
    reason: str

    @property
    def ok(self) -> bool:
        return self.band in ACCEPTED[self.expected]

    @property
    def verdict(self) -> str:
        return "match" if self.ok else ("by design" if self.persona in BY_DESIGN else "MISMATCH")


class PersonaClient:
    """Pins one CRS test person, so enrollment asks about exactly that person. Everything else about
    the path (the products, their order, the parsing, the banding) is the real one."""

    def __init__(self, inner, persona: str):
        self.inner, self.persona_id = inner, persona
        self.mocked = inner.mocked

    def _pin(self, name):
        return lambda *a, **k: getattr(self.inner, name)(self.persona_id, *a[1:], **k)

    def data_prefill(self, principal_id):
        return self.inner.data_prefill(self.persona_id)

    def data_append(self, principal_id):
        return self.inner.data_append(self.persona_id)

    def identity_verification(self, principal_id, prefill):
        return self.inner.identity_verification(self.persona_id, prefill)

    def fraud_finder(self, principal_id):
        return self.inner.fraud_finder(self.persona_id)

    def credit_report(self, principal_id):
        return self.inner.credit_report(self.persona_id)


def _client(live: bool, persona: str):
    """The same enrollment path either way: the live sandbox answers for the real test person, and
    `PersonaCrs` answers from the outcomes CRS publishes for that person."""
    if not live:
        return PersonaCrs(persona)
    missing = [k for k in ("CRS_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"the live sandbox needs {', '.join(missing)} in .env")
    return PersonaClient(HttpCrs(os.environ["CRS_BASE_URL"], os.environ["CRS_USERNAME"],
                                 os.environ["CRS_PASSWORD"], {}, set()), persona)


def run(live: bool = False) -> list[Row]:
    rows = []
    for pid, person in PERSONAS.items():
        enrollment = CrsEnrollment(_client(live, pid))
        profile = enrollment.profile(pid)
        band, reasons = band_for(profile)
        rows.append(Row(pid, person["name"], person["profile"], person["decision"], band,
                        reasons[0] if reasons else ""))
    return rows


def report(rows: list[Row], live: bool) -> str:
    bureau = "the live CRS sandbox" if live else "CRS's published outcomes for each test person"
    in_scope = [r for r in rows if r.persona not in BY_DESIGN]
    matched = sum(r.ok for r in in_scope)
    unexplained = [r for r in rows if r.verdict == "MISMATCH"]
    out = [f"# CRS persona conformance ({bureau})", "",
           f"**{matched} of {len(in_scope)}** of the identity-and-fraud personas land where CRS says "
           f"they should. The other {len(BY_DESIGN)} are deliberate: CRS's expected decision there is "
           "a lending or tenant-screening decision, and ours is a fraud decision about an agent's "
           "spending. Each is explained below.", "",
           "| persona | who they are | CRS expects | our band | verdict | why |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r.persona} | {r.name.title()} — {r.profile} | {r.expected} | {r.band} | "
                   f"{r.verdict} | {r.reason} |")
    out += ["", "## The five deliberate deviations", ""]
    for pid, why in BY_DESIGN.items():
        row = next(r for r in rows if r.persona == pid)
        out.append(f"- **{pid} {row.name.title()}** — CRS expects {row.expected}, we give a "
                   f"{row.band} line. {why}")
    out += ["", "Bands come from `engine/trust_line.py`. The mapping from CRS's four decisions to the "
            "bands that satisfy them, and these five deviations, are at the top of "
            "`engine/crs_conformance.py`."]
    if unexplained:
        out += ["", f"**{len(unexplained)} unexplained mismatch(es):** "
                + ", ".join(r.persona for r in unexplained)]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the real CRS sandbox, not the mock bureau")
    parser.add_argument("--out", default="", help="also write the table to this file")
    args = parser.parse_args()

    rows = run(args.live)
    text = report(rows, args.live)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"written to {args.out}")
    return 1 if [r for r in rows if r.verdict == "MISMATCH"] else 0


if __name__ == "__main__":
    sys.exit(main())

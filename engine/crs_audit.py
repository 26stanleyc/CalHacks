"""The CRS audit log: which bureau calls we made about a person, and why.

Pulling a consumer's file is regulated. The FCRA expects whoever pulled it to be able to say, later,
what they asked for, about whom, and under what permissible purpose; CRS keeps its own retention
record for exactly that reason, and its guidance says a production version of any of this would need
a permissible purpose and the consumer's consent.

Every person here is one of CRS's fake sandbox test identities, so nothing in this log is a real
consumer's data. The log exists because a fraud product that reads bureau data and *cannot* answer
"what did you look up about me?" is not one anyone could deploy.

Two sides, which should agree:
- ours: every call enrollment made (`CrsEnrollment.audit`) and every escalation check, with the
  purpose each was made under;
- CRS's: `GET /users/logs` and `GET /users/retention/{id}` on the live sandbox, which is CRS's own
  record of the same calls, keyed by the RequestID they returned.

    uv run python -m engine.crs_audit                 # the mock bureau, for the shape
    CRS_ESCALATION=live uv run python -m engine.crs_audit --compare   # ours against CRS's own log
"""

import argparse
import os
from collections import Counter

from engine.crs import HttpCrs

ENROLLMENT_PURPOSE = "agent enrollment: identity of the human behind the agent"
ESCALATION_PURPOSE = "payment review: an uncertain payment by this person's agent"


def our_log(enrollment=None, escalation=None) -> list[dict]:
    """Every CRS call this run made, in order, with the purpose it was made under."""
    rows = list(enrollment.audit) if enrollment is not None else []
    client = getattr(enrollment, "client", None) or getattr(escalation, "client", None)
    # The escalation path records its calls on each decision, not centrally; its client's own
    # request list is the complete record of what actually went to CRS.
    for call in getattr(client, "requests", []):
        rows.append({"principal_id": "", "product": call.get("product", ""),
                     "purpose": ESCALATION_PURPOSE, "request_id": call.get("request_id", ""),
                     "mocked": False})
    return rows


def summarize(rows: list[dict]) -> str:
    by_product = Counter(r["product"] for r in rows)
    people = {r["principal_id"] for r in rows if r.get("principal_id")}
    with_ids = sum(1 for r in rows if r.get("request_id"))
    out = ["# CRS audit log", "",
           f"{len(rows)} bureau calls about {len(people)} people. "
           f"{with_ids} carry a CRS RequestID (live calls only; the mock bureau has none).", "",
           "| product | calls |", "|---|---|"]
    out += [f"| {product} | {n} |" for product, n in sorted(by_product.items())]
    out += ["", "| purpose | calls |", "|---|---|"]
    out += [f"| {purpose} | {n} |" for purpose, n in sorted(Counter(r["purpose"] for r in rows).items())]
    return "\n".join(out) + "\n"


def compare_with_crs(client: HttpCrs, rows: list[dict]) -> str:
    """Our record against CRS's own. Theirs is the one that would be audited."""
    try:
        theirs = client.logs()
    except Exception as e:  # a refused login, a missing role, a rate limit
        return f"\nCRS's own log could not be read: {e}\n"
    ours = [r for r in rows if r.get("request_id")]
    out = ["", "## Against CRS's own record", "",
           f"- ours: {len(ours)} calls with a RequestID", f"- CRS's `/users/logs`: {len(theirs)} entries"]
    retained = 0
    for row in ours[:5]:  # a sample: every retention read is itself a call
        try:
            if client.retention(row["request_id"]):
                retained += 1
        except Exception:
            pass
    out.append(f"- {retained} of the first {min(5, len(ours))} checked against "
               f"`/users/retention/{{id}}` and found retained")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true", help="also read CRS's own /users/logs")
    parser.add_argument("--out", default="", help="write the summary here")
    args = parser.parse_args()

    from contracts.schemas import IdentityStatus
    from engine.crs import MockCrs
    from engine.crs_enrollment import CrsEnrollment

    live = os.environ.get("CRS_ESCALATION", "").strip().lower() == "live"
    if live:
        client = HttpCrs(os.environ["CRS_BASE_URL"], os.environ["CRS_USERNAME"], os.environ["CRS_PASSWORD"], {}, set())
    else:
        client = MockCrs({f"p{i}": IdentityStatus.VERIFIED for i in range(4)}, {f"p{i}" for i in range(4)})
    enrollment = CrsEnrollment(client)
    for i in range(4):
        enrollment.profile(f"p{i}")

    rows = our_log(enrollment)
    text = summarize(rows)
    if args.compare and isinstance(client, HttpCrs):
        text += compare_with_crs(client, rows)
    print(text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# CRS: what it does here, and what it is worth

CRS answers the question this whole project is built on. Fraud detection used to ask "is this the
real cardholder?"; with agents, the agent *is* the legitimate holder of its credential, so the
question becomes **which real human is responsible for this agent, and is this what they asked
for?** The second half is the mandate specialist. The first half is CRS.

## Where CRS sits

Every person is resolved **once**, when their agent first appears, not when a payment later looks
odd (`engine/crs_enrollment.py`). From that one lookup:

- **the identity specialist votes with the bureau's answer on every payment** — five features
  (`i_crs_verified`, `i_crs_fraud_score`, `i_crs_file_depth`, `i_crs_email_age_days`,
  `i_crs_phone_risk`) instead of two values the simulator used to invent;
- **the graph specialist sees accounts made in bulk** — CRS resolves a person to a phone and an
  inbox, and a batch of accounts made by one operator shares them
  (`g_crs_principals_per_contact`);
- **the trust line underwrites the mandate** — the band CRS's answer earns sets how much of the
  limit the human asked for their agent actually gets (`engine/trust_line.py`);
- **the investigator can ask** — `identity_check` is CRS-backed and `crs_trust_line` says what the
  agent was granted and why, so a case file shows the bureau's reasoning.

The old escalation path (CRS on payments already in the uncertain band) still runs on top. It was
reaching 17 of 1,200 payments, about 1.4%.

Cost: at most 5 CRS calls per person, once. The sandbox answers each of its 20 test persons the
same way every time, so a run of any size needs at most 20 x 5 real calls.

## What it is worth

Identical traffic and seeds, 4 rounds, 3 seeds, mock bureau ([ablation.md](ablation.md),
`uv run python -m engine.crs_ablation`):

| CRS | fraud settled | fraud stopped | false positives |
|---|---|---|---|
| off (where we started) | $20,813 | 47.6% | 1.11% |
| escalation only (uncertain band) | $22,926 | 46.6% | 0.97% |
| **enrolled (every person, every payment)** | **$9,472** | **71.5%** | **1.43%** |

Two things to read here, and the second one matters more than the first:

1. **Enrolled CRS settles 54% less fraud** and stops 71.5% of it against 47.6%, while staying under
   the project's 2% false-positive cap.
2. **CRS as an escalation path was worth nothing measurable.** It is slightly *worse* than off, well
   inside noise. That is the honest verdict on CRS as a bolt-on, and it is the reason this work
   exists: the same data, asked earlier and about the person rather than the payment, is the
   difference between no effect and halving the fraud.

Most of the gain is the trust line. An identity the bureau cannot stand behind is held to a $5
floor, so its payments are refused for being over the mandate's cap before any scoring happens:
215 such refusals a run against 38 with CRS off.

## The bands were measured, not guessed

The first version cut a standard-band agent to 60% of its requested limit and a starter to 25%,
which reads like sensible underwriting. It pushed false positives to **3.84%**, through the 2% cap,
and bought no fraud reduction. The reason is in the data: across three seeds, 65 of 315 honest
shoppers land in standard and 14 in starter, and **no honest shopper lands in restricted**. The cuts
were landing almost entirely on honest people.

So the trust line now bites only where the bureau has an actual concern. A verified identity with a
file keeps the limit its owner asked for; having less credit history is a reason to lend someone
less money, and this is not a loan.

One rule holds above all of it: **the trust line never raises a cap.** The human's mandate is the
ceiling and the bureau can only come down from it. A good credit file is never a reason to let an
agent spend more than its owner allowed.

## Conformance against CRS's own test persons

[conformance-mock.md](conformance-mock.md), `uv run python -m engine.crs_conformance`.

CRS ships 20 sandbox test persons, each carrying the decision it should produce, and asks teams to
"build your decision logic so each persona lands on its expected decision". **15 of 15 of the
identity-and-fraud personas land where CRS says they should.**

The other five deviate on purpose, and the suite names each one. CRS's expected decisions are a
lender's or a landlord's; ours is a fraud decision about an agent's spending. The clearest case is
P09, which CRS's own summary calls a "typical rental screening decline": clean credit, verified
identity, declined on a felony conviction. We give that agent a full line. A conviction is not
evidence that a payment is fraud, and screening payers on one would be both wrong and useless here.
P08 would need PitchPoint bankruptcies/liens/judgments, which we also do not call, for the same
kind of reason.

Running it against the live sandbox is one flag: `CRS_ESCALATION=live uv run python -m
engine.crs_conformance --live`.

## The audit log

[audit-log-mock.md](audit-log-mock.md), `uv run python -m engine.crs_audit`.

Which bureau calls we made, about whom, under what purpose, and — live — the same checked against
CRS's own `/users/logs` and `/users/retention/{id}` by RequestID. Pulling a consumer's file is
regulated, and a fraud product that reads bureau data and cannot answer "what did you look up about
me?" is not one anyone could deploy. Every person here is one of CRS's fake sandbox identities; a
production version would need a permissible purpose and the consumer's consent, and the trust line's
soft pull at enrollment (a limit the consumer is asking for) is a different purpose from pulling a
file to judge a transaction.

## What we deliberately do not use

Criminal records, eviction records and PitchPoint bankruptcies/liens/judgments are all in the
sandbox and all easy to call. We do not call them on buyers. They answer "would you lend to this
person, or rent to them", not "is this payment theirs", and screening payments on a criminal record
would add surface area while making the product worse. The one defensible use is on *merchant*
operators, which is noted as future work rather than claimed.

Credit reports are pulled for one thing only: the trust line, at enrollment. A clean credit file
says nothing about whether an agent was hijacked an hour ago, so a credit score never scores a
payment.

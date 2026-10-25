# CRS sandbox: first live calls

Run Sat Oct 24, about 20:00 PT. One login to `https://api-sandbox.stitchcredit.com/api` as `CalhacksDev11`
(after two refused logins, CRS113, while the credentials were being sorted out), then each identity product
once on CRS's sandbox test person **P01** (Margaret Okafor, "Prime"), with the exact bodies from CRS's
`@crscreditapi/crs-calhacks-mcp-server` 1.1.2 personas. Saved responses: `engine/tests/fixtures/crs/`.

| Product | Endpoint | HTTP | RequestID | Result |
|---|---|---|---|---|
| Data Prefill (AccurateAppend reverse) | `POST /accurate-append/reverse` | 200 | e9d69356-3cd6-4292-b6cd-90ff7921726d | 1 record: name and current address |
| Identity Verification (TransUnion TruValidate) | `POST /transunion/identity-manager-verification/basic` | 200 | aa65deac-7da6-40ac-9a67-e0755c9e55de | no messages in `addOnProducts[0].idManagerVerification` = verified (the extra `idmanagerVerAnalysisV2` block says MEDIUM with an address alert; CRS's guide doesn't use it for the verdict) |
| FraudFinder (AtData) | `POST /fraud-finder/fraud-finder` | 200 | b154290a-2db3-49c6-a97e-5503132475f8 | risk score 11 (low), email valid, phone mobile, name and address match |

Through `CrsEscalation` these three answers allow an in-band payment (identity verified, risk low).
No credit-report endpoint was called.

## Recorded run with live CRS: `h2h-real-42-202610250153`

Real models (Bitdeer), seed 42, 4 rounds, the merged design plus the CRS step, recorded in the team
database. Live CRS for this process only (`CRS_ESCALATION=live` on the command line; `.env` stays at 0).
Band 0.30-0.70, at most 20 checks a round, forward append from 0.50. 348 model calls, 2.16M tokens,
9.4 minutes a round. Head-to-head table: [head-to-head/bitdeer-crs-live.md](head-to-head/bitdeer-crs-live.md).

| | |
|---|---|
| CRS checks | 23 (3 in round 3, 20 in round 4, which hit the cap), all on fraud payments, all live (`mocked: false`) |
| CRS decision | 13 block (identity failed), 10 step up (identity review plus FraudFinder medium) |
| Honest shoppers checked | 0 |
| Payers hiding phone and email | 11: 3 got a forward append (score at least 0.50), 8 verified identity only |
| Sandbox test persons used | P14 (deceased SSN) 10, P16 (fraud alert) 10, P13 (synthetic) 2, P12 (mismatch) 1 |
| Watch-list additions | 0 (no FraudFinder score reached high) |
| CRS unavailable | 0 (one login, no refusals) |

Real TransUnion reasons came through, e.g. "input ssn belongs to a deceased person" and "input ssn not
associated with input name; input date of birth does not match file".

**Where the band sits.** The learned combiner (from round 3) gives calibrated probabilities with a low base
rate, so the uncertain zone is lower than 0.30:

| round | honest under 0.10 | honest 0.10-0.30 | honest 0.30-0.70 | fraud 0.10-0.30 | fraud 0.30-0.70 |
|---|---|---|---|---|---|
| 3 | 322 | 0 | 0 | 94 | 3 |
| 4 | 319 | 3 | 1 | 32 | 153 |

In round 4 the 20-check cap left 134 in-band payments unchecked. The cap is spec 11's limit for paid
calls; sandbox answers for the test persons are free and cached (this run needed a handful of distinct
requests).

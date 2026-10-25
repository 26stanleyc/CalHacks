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

# CRS persona conformance (CRS's published outcomes for each test person)

**15 of 15** of the identity-and-fraud personas land where CRS says they should. The other 5 are deliberate: CRS's expected decision there is a lending or tenant-screening decision, and ours is a fraud decision about an agent's spending. Each is explained below.

| persona | who they are | CRS expects | our band | verdict | why |
|---|---|---|---|---|---|
| P01 | Margaret Okafor — Prime | APPROVE | full | match | identity verified, score 790 at 3 of 3 bureaus, fraud risk low |
| P02 | Daniel Whitfield — Super-prime mortgage holder | APPROVE | full | match | identity verified, score 815 at 3 of 3 bureaus, fraud risk low |
| P03 | Jasmine Reyes — Thin file / Gen Z | REVIEW | standard | match | identity verified, score 660, fraud risk low |
| P04 | Tomas Lindqvist — Credit invisible | NO_FILE | starter | match | no credit file at any bureau |
| P05 | Ricky Dalton — Subprime | DECLINE | starter | by design | score 540: capacity for a starter line only |
| P06 | Angela Morrissey — Chapter 7 discharged | REVIEW | starter | match | score 580: capacity for a starter line only |
| P07 | Marcus Bellamy — Chapter 13 active | DECLINE | starter | by design | score 560: capacity for a starter line only |
| P08 | Priya Venkatesan — Tax lien and civil judgment | REVIEW | full | by design | identity verified, score 690 at 3 of 3 bureaus, fraud risk low |
| P09 | Derek Holloway — Felony conviction | DECLINE | full | by design | identity verified, score 700 at 3 of 3 bureaus, fraud risk low |
| P10 | Lauren Kowalczyk — Old dismissed misdemeanor | APPROVE | full | match | identity verified, score 720 at 3 of 3 bureaus, fraud risk low |
| P11 | Brandon Szabo — Eviction history | DECLINE | starter | by design | score 610: capacity for a starter line only |
| P12 | Victor Anand — Identity mismatch | DECLINE | restricted | match | identity verification failed at the bureau |
| P13 | Kayla Brennan — Synthetic identity / fraud | DECLINE | restricted | match | identity verification failed at the bureau |
| P14 | Harold Pettigrew — Deceased indicator | DECLINE | restricted | match | identity verification failed at the bureau |
| P15 | Nicole Farragut — Security freeze at one bureau | REVIEW | standard | match | a bureau file is frozen, so the person cannot be fully assessed |
| P16 | Samuel Oyelaran — Fraud alert on file | REVIEW | standard | match | identity in review, score 730, fraud risk medium |
| P17 | Elena Castellanos — Bureau divergence | REVIEW | standard | match | only 2 of 3 bureaus could score this file |
| P18 | Terrence Abernathy — Rebuilding | REVIEW | standard | match | identity verified, score 650, fraud risk low |
| P19 | Grace Nakamura — Small-business owner | APPROVE | full | match | identity verified, score 770 at 3 of 3 bureaus, fraud risk low |
| P20 | Dolores Whitcomb — Retiree with stale file | REVIEW | standard | match | only 1 of 3 bureaus could score this file |

## The five deliberate deviations

- **P05 Ricky Dalton** — CRS expects DECLINE, we give a starter line. declined on creditworthiness (subprime). A thin wallet earns a smaller line, not a refusal: we are setting a spending limit, not lending money.
- **P07 Marcus Bellamy** — CRS expects DECLINE, we give a starter line. declined on an active Chapter 13. Same reason: it lowers capacity, and capacity is a limit, not evidence that a payment is fraud.
- **P11 Brandon Szabo** — CRS expects DECLINE, we give a starter line. declined on eviction history, which is a landlord's question. It says nothing about whether this person's agent was hijacked.
- **P09 Derek Holloway** — CRS expects DECLINE, we give a full line. declined on a felony conviction with clean credit and a verified identity. We do not run criminal checks on buyers: a conviction is not evidence that a payment is fraud, and screening payers on one would be both wrong and useless here.
- **P08 Priya Venkatesan** — CRS expects REVIEW, we give a full line. a tax lien and a civil judgment, which live in PitchPoint bankruptcies/liens/judgments. We do not call that product: it speaks to whether someone pays their debts, not to whether this payment is theirs.

Bands come from `engine/trust_line.py`. The mapping from CRS's four decisions to the bands that satisfy them, and these five deviations, are at the top of `engine/crs_conformance.py`.

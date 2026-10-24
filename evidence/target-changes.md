# Target changes

Every change to a numeric target from spec 8.2, with a one-line reason (spec 8.1).

- **2026-10-24, M4 freeze test (Stanley).** Spec: "freeze rounds 5-7: round 7's loss is higher than round 4's;
  by round 10 the loss is within 10% of round 4's". Measured (seed 42, mock brains): the defense is still
  improving at round 4, because the XGBoost voter earns its weight over rounds 2-4 as out-of-sample evidence
  accrues. So even a frozen round 7 ($1,204) sits below round 4 ($2,472), although freezing clearly costs money
  (rounds 6-8: $1,489 / $1,204 / $1,178 frozen vs $1,276 / $852 / $124 unfrozen). New check: the frozen rounds
  lose more than the same rounds unfrozen on the same seed, and by round 10 the frozen run is back within 10%
  of round 4's loss or below it.

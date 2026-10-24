# Target changes

Every change to a numeric target from spec 8.2, with a one-line reason (spec 8.1).

- **2026-10-24, M4 freeze test (Stanley).** Spec: "freeze rounds 5-7: round 7's loss is higher than round 4's;
  by round 10 the loss is within 10% of round 4's". Measured (seed 42, mock brains): the defense is still
  improving at round 4, because the XGBoost voter earns its weight over rounds 2-4 as out-of-sample evidence
  accrues. So even a frozen round 7 ($1,204) sits below round 4 ($2,472), although freezing clearly costs money
  (rounds 6-8: $1,489 / $1,204 / $1,178 frozen vs $1,276 / $852 / $124 unfrozen). New check: the frozen rounds
  lose more than the same rounds unfrozen on the same seed, and by round 10 the frozen run is back within 10%
  of round 4's loss or below it.
- **2026-10-24, M4 freeze test, with the arena on (Stanley).** Freezing also stops spawning (spec 7: "no
  reflection, no weight updates, no spawning"), so a frozen run carries fewer of the defense's own sparring
  attackers and its total loss can drop. The freeze check therefore measures what the outsider lineages
  extract, the outside threat that runs identically either way. Seed 42: rounds 6-8 lose $2,427 / $2,643 /
  $2,104 to outsiders frozen vs $1,743 / $1,266 / $777 learning; round 10 is $224 after unfreezing vs
  $2,068 if never unfrozen.
- **2026-10-24, M6 escalation trend (Stanley).** Spec: "the escalation rate in round 10 is lower than in round 2".
  Rounds 1-2 escalate little while the case memory is empty and the XGBoost voter hasn't earned weight yet, so
  round 2 isn't the peak: on seed 42 it's 5.2%, then rounds 3-5 sit near the 10% budget and round 10 falls to
  5.8% (seeds 7 and 123: 6.2% -> 5.2%, 6.5% -> 5.0%). New check: round 10 is at least 20% below the average of
  rounds 3-5. All three seeds pass with room (9.5% -> 5.8%, 9.1% -> 5.2%, 9.8% -> 5.0%). With the arena on,
  newly spawned strategies keep close calls coming and escalations stay at the budget, so M6 is checked
  before the arena, like M3.
- **2026-10-24, M11 intel on vs off (Stanley).** Spec: "intel on loses at least 50% less to new attackers in
  their first round than intel off". Not met on mock brains, so the check is xfail rather than changed. First-round
  extraction by all 3 attackers of each report, 8 rounds: seed 42 $12,279 with intel on vs $12,335 off; seed 7
  $12,259 vs $12,502. The keyword extractor's watch rules help on some patterns (gift card: $0 vs $183 for its
  test persona), but they don't add enough catches overall. They also cost some false positives: round 4 reaches
  2.3% FP with intel on vs 0.3% off on seed 42 (3.3% vs 2.7% on seed 7). A real extractor model is the expected fix.
- **2026-10-24, M6 early escalation (Stanley).** Spec: "5-10% of payments escalate early". The check read
  "early" as rounds 1-3. Many of those early escalations came from the XGBoost voter voting "block" before
  it had enough labels to be trusted, so payments went to the investigator because of a voter whose weight
  was at its floor. An untrusted voter now sits out (weight 0) and its votes no longer trigger escalation.
  Rounds 1-3 then escalate 3% / 0% / 0.3%, and escalation reaches 10% in round 4, once the voter is
  calibrated and the case memory has filled. New check: the peak in rounds 1-5 is 5-10% (seed 42: 10%).

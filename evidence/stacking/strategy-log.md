# Strategy log: beating retrained XGBoost and rules + XGBoost

Goal: beat `xgboost` (retrained) and `rules_plus_xgboost` on every head-to-head metric:
- new-attack cost (first round, first 3 rounds)
- total cost
- mean and max false-positive rate, with our max kept at or under the 2% cap
- recall at 1% FPR and PR-AUC

Rules:
- Tune only on dev seeds 42–44. Report final numbers on fresh seeds never used for tuning.
- Baseline settings (`bench/config.py`) are never changed.
- A win that breaks the 2% cap doesn't count.

Setup: mock models, 10 rounds, the head-to-head replay script (all contenders on the full system's traffic, same logged features, XGBoost retrained on the fixed schedule), plus a held-out exam. The exam uses 6 hand-written fraud patterns that no defense trained on, mixed into fresh honest traffic with 3 warm-up days.

Metric definitions match `engine/head_to_head.py`: honest mandate breaches are excluded from false positives, and "new attacks" are spawned and threat-intel attackers first seen after round 3.

## S0: previous system (noisy-OR combiner, XGBoost voter, no anomaly voter)
Dev seeds, full tuning tries:
- fraud caught: 55% over all rounds, 66% over the last 3
- total cost: $12.5k per round
- mean FP: 1.0%
- held-out exam: 57% caught

## S1: learned combiner (stacking) + anomaly voter (Isolation Forest)
**What changed:**
- The orchestrator's noisy-OR is replaced by a logistic regression over:
  - every voter's score
  - pairwise products of the scores
  - log amount
- It's trained on votes as cast, so every score is out of sample.
- It's calibrated so honest payments, re-scored by the about-to-deploy defense, stay under 60% of the FP cap. It falls back to the noisy-OR when it can't calibrate.
- New anomaly voter: an Isolation Forest on recent honest payments, with vectorized scoring at 0.06 ms per payment.

**First fix:** the first version calibrated on stale votes, and FP hit 22% in round 3. Calibrating on re-scored honest traffic fixed it.

**Dev result (full tuning tries; the XGBoost-alone row of this run was invalid because of a script bug, since fixed):**

| | caught (all / last 3) | cost per round (all / last 3) | mean FP | recall@1% (all) | PR-AUC (all) | held-out caught |
|---|---|---|---|---|---|---|
| S1 full system | 68% / 87% | $7.6k / $2.0k | 1.1% | 0.73 | 0.92 | 52% |
| rules + XGBoost | 75% / 89% | $7.2k / $2.0k | **2.8%** (over the cap) | 0.66 | 0.94 | 61% (FP 3.7%) |
| S0 previous system | 55% / 66% | $12.5k / $6.4k | 1.0% | 0.65 | 0.89 | 57% |

**Read:** a big gain over S0 (cost −39%). It matches rules + XGBoost on cost, while that baseline breaks the FP cap. It's still behind on PR-AUC and on the held-out exam.

**Tests:** two marginal failures, both from the defense getting sharper:
- M6: escalations peak at 4.75% in early rounds vs a 5% minimum.
- M7: spawned attackers beat outsiders in 4 of 9 rounds vs 4.5 required.

### S1 screening run (dev seeds, arena tuning tries 40, fixed script)

| metric | S1 | XGBoost | rules + XGBoost |
|---|---|---|---|
| recall@1% FPR | **0.745** | 0.675 | 0.675 |
| PR-AUC | 0.924 | 0.900 | **0.941** |
| mean FP | **0.81%** | 1.44% | 2.50% |
| max FP | **1.67%** | 4.80% | 5.91% |
| total cost (10 rounds) | $67.6k | $72.6k | **$62.4k** |
| new attacks, first round | **$12.1k** | $14.0k | $13.3k |
| new attacks, first 3 rounds | **$14.7k** | $18.2k | $17.4k |

Beats XGBoost on all metrics. Loses to rules + XGBoost on PR-AUC and total cost.

**Where the cost gap comes from:**
- **Rounds 2–3.** We catch 33–36% there, against 58–76% for rules + XGBoost, which runs at 5% FP in those rounds. Our XGBoost voter sits out until 3 rounds of out-of-sample history exist, and the combiner only starts in round 3.
- **Rounds 4–10** are mixed, and we win the last 3.

## S2: out-of-fold stacking from round 1, plus log-odds inputs: **rejected**
**What changed:**
- The extra voters' training scores came from 5-fold out-of-fold copies, not votes as cast.
- The combiner was allowed after 1 round.
- Each voter's log-odds was added as an input.

**Dev screen:**
- PR-AUC 0.949: beats both baselines.
- **But round 2's FP hit 4.5%, breaking the cap.**
- Catch fell in later rounds.
- Total cost rose to $77.2k, and new-attack cost to $21.6k.

**Why:**
- Random folds share a round and its attackers, so they overstate how well XGBoost generalizes to next round's adapted attackers.
- Round 2 also differs from round 1 for honest traffic: agent baselines only exist after round 1.

The votes as cast are out of sample in time, which is the right target. Out-of-fold is kept behind `OUT_OF_FOLD = False`.

## S3: S1 plus log-odds inputs: **rejected**
**Dev screen:**
- PR-AUC 0.926, vs 0.924 for S1: no real gain.
- Total cost $65.3k vs rules + XGBoost's $57.1k.
- **Worst-round FP 3.4% on seed 44: over the cap.**
- More extreme scores make the calibration less stable.

## S4: S3 with the FP target raised from 60% to 75% of the cap: **rejected**
- Total cost $65.0k, catch 72%.
- **Worst-round FP 4.0%: over the cap.**

## Noise warning (applies to every comparison here)
- The traffic depends on our defense, since attackers adapt to it. So each run gives the baselines different traffic too: XGBoost's total cost ranged from $66.5k to $72.6k across S1–S4 on the same seeds.
- New-attack cost is especially noisy. It also leans toward the baselines: spawned attackers are tuned against *our* defense in the sandbox, then replayed on baselines that never faced attackers tuned against them.
- FP is measured on about 300 honest payments a round, so a single flagged shopper moves it by 0.33 points.

## S5: S1 combiner (no log-odds), calibrated on the worst of the last 3 honest rounds: **kept**

| metric | S5 | XGBoost | rules + XGBoost |
|---|---|---|---|
| recall@1% FPR | **0.743** | 0.669 | 0.669 |
| PR-AUC | 0.923 | 0.900 | **0.940** |
| mean FP | **0.81%** | 1.39% | 2.49% |
| max FP | **1.78%** (worst seed 2.0%) | 4.80% | 5.91% |
| total cost | $67.4k | $72.2k | **$62.0k** |
| new attacks, first round | **$11.7k** | $13.2k | $12.7k |
| new attacks, first 3 rounds | **$14.5k** | $17.7k | $16.9k |

**FP now stays at or under the cap on every seed.**
- Beats XGBoost on all metrics.
- Beats rules + XGBoost on 5 of 7. It loses on PR-AUC and total cost.

**Cost by round:**

| | rounds 2–3 | rounds 4–10 |
|---|---|---|
| S5 | $34.1k | **$29.5k** |
| rules + XGBoost (at 5% FP in rounds 2–3) | **$25.9k** | $32.4k |

**Round 3:** our FP is 0.0% on every seed. The combiner is calibrated against round 1, a warm-up round without agent history, so it's far too cautious.

## S6: leave the warm-up round (round 1) out of calibration: **neutral, kept**
Results were essentially the same as S5: total cost $68.2k, PR-AUC 0.924, FP max 1.78%, round 3 FP still 0%. Kept because it's principled: cold-start traffic shouldn't set the calibration.

**PR-AUC by round (S5/S6):**
- From round 3 on, we match or beat rules + XGBoost every round.
- The whole gap is round 2: **0.64 vs 0.92**. In round 2 our system can't use XGBoost yet (no out-of-sample votes). The baselines use an XGBoost trained on round 1, at 4–5% FP.

## S7: a cautious first combiner after round 1 (out-of-fold, FP target a quarter of the cap): **rejected as is**
- **PR-AUC 0.947: beats both** (rules + XGBoost 0.937, XGBoost 0.896). Round 2 PR-AUC went from 0.64 to 0.89.
- Total cost $65.3k vs rules + XGBoost's $59.3k: still about $6k behind, as in S5 (both sides' costs vary between runs).
- **Round 2 FP hit 3.7% on seed 44: over the cap.**

## S7b: S7 with the bootstrap's FP target at a tenth of the cap: **promising, one cap blemish**

| metric | S7b | XGBoost | rules + XGBoost |
|---|---|---|---|
| recall@1% FPR | **0.763** | 0.663 | 0.676 |
| PR-AUC | **0.950** | 0.897 | 0.938 |
| mean FP | **0.74%** | 1.46% | 2.53% |
| max FP (mean over seeds) | **1.79%** | 4.80% | 6.02% |
| total cost | $65.4k | $72.3k | **$62.3k** |
| new attacks, first round | **$11.8k** | $13.5k | $12.7k |
| new attacks, first 3 rounds | **$15.2k** | $18.5k | $17.6k |

- Round 2 FP is now at most 1.7% on every seed.
- **Seed 44's last round hit 2.3%** (7 of 300 honest payments flagged, against the 6 allowed): calibration drift in the regular combiner, not the bootstrap.
- Total cost is the only loss. The gap is $3.1k, down from $5.4k in S5, and it all comes from rounds 2–3, where rules + XGBoost runs at 5% FP.

## S8: S7b with the regular combiner's FP target lowered from 60% to 50% of the cap: **rejected**
- FP stays under the cap. The worst seed's 2.02% is round 1 (seed rulebooks, before any learning); static rules flag the same shoppers there.
- But it gives up too much: total cost $70.7k, and both new-attack metrics now lose to both baselines.

## Final configuration: S7b
- Combiner FP target: 60% of the cap.
- Bootstrap after round 1: a tenth of the cap.
- Warm-up round left out of calibration.
- Anomaly voter on.

Between-run noise is about as large as the differences between S7b, S8 and the nearby settings, so tuning further on dev seeds would mostly fit noise. The final numbers come from fresh seeds 100–104, never used for tuning, with the full arena tuning budget.

## Final evaluation on fresh seeds 100–104 (never used for tuning; full arena tuning budget)

| metric | ours (S7b) | XGBoost | rules + XGBoost | previous system |
|---|---|---|---|---|
| recall@1% FPR | **0.732** | 0.661 | 0.647 | — |
| PR-AUC | **0.950** | 0.896 | 0.938 | — |
| mean FP | **0.84%** | 1.38% | 2.45% | — |
| max FP (mean over seeds) | **2.87%** | 4.28% | 5.21% | — |
| total cost (10 rounds) | $65.6k | $71.6k | **$61.0k** | $134.6k |
| new attacks, first round | **$11.0k** | $12.2k | $11.8k | — |
| new attacks, first 3 rounds | **$13.0k** | $15.0k | $14.6k | — |
| held-out exam caught | 55% | 17% | **63%** | 60% |

### Honest reading
- **Against the previous system:** a big improvement. Total cost fell from $134.6k to $65.6k (−51%), and catch rose from 52% to 70%.
- **Against XGBoost:** better on every target metric.
- **Against rules + XGBoost:** better on 6 of 7 metrics. It loses total cost by $4.6k, and the loss sits in rounds 2–3, where that baseline runs at about 5% FP.

### But our system did not hold the 2% FP cap on the fresh seeds
- **Round 2 (the bootstrap combiner):** 1.7–3.0%. Over the cap on seeds 103 and 104.
- **Round 4:** 2.3–4.7%. Over the cap on 4 of 5 seeds.
- Our max FP is still lower than both baselines', but by the rules a win that comes with breaking the cap doesn't count.

### Two methodology problems
1. **Screening runs used 40 arena tuning tries; the final run used the full budget.** Stronger spawned attackers lead to more aggressive rule changes and voter retraining, and the calibration (done before the voters retrain) didn't anticipate that. Tuning should have used the full budget.
2. **Calibration scores honest traffic with the voters as they were before this round's retraining.** The deployed voters are retrained afterward, so their scores on the next round can drift from what calibration saw.

**Status: not yet a clean win.** Next fix: a hard FP guard that holds whatever the drift (see the report).

## Tests on the final configuration
377 passed, with 1 failure and 1 error:
- **M6 (failure):** early escalation rate peaks at 4.25% vs a 5% minimum. The combiner is more decisive, so fewer payments are close calls.
- **M0 (error):** the Bureau's port was taken by other runs on this machine. Environmental, not a regression.

# Round 2 of tuning: a hard false-alarm guard (full arena budget from here on)

## Diagnosis of the fresh-seed breaches (S7b config, seed 100, now a used seed)
**Round 4:** 9 of 299 honest payments flagged (3.0%).
- **All 9 were flagged by the XGBoost voter alone**, with no rule firing.
- 7 were large ($95–150) payments sent to step-up at fraud probabilities of only 0.12–0.43. Under the cost matrix, a step-up is cheap next to losing $100.

**Cause:** the XGBoost voter had just been retrained on round 3's stronger spawned attacks, which made it aggressive on honest big-ticket payments. Calibration had scored honest traffic with the voter from *before* that retrain, so it couldn't see this.

## G1: leave-one-round-out calibration + feedback controller: **rejected (too cautious)**
- Calibration scored each round's honest payments with voter copies trained on every other round.
- **FP held:** max 2.0%, and that's round 1 (seed rules, before learning).
- **But too pessimistic:** shifts of 4.5–6.5, real FP 0.42% mean, catch 61%, total cost $99.8k. It lost 4 metrics.

## G2: held-out-fold calibration + feedback controller (candidate)
**Part 1:** each calibration round's honest payments are scored by voter copies trained on everything the deployed voter is trained on (newest round included), except a held-out third of that round. That's nearly the deployed model, on honest traffic it hasn't seen.

**Part 2 (feedback controller):** after each round, compare the FP the combiner actually made with the cap.
- At or above 75% of the cap, the target tightens (×0.6, down to a quarter of `STACK_AT`).
- Below 25% of the cap, it relaxes (×1.1, back up to `STACK_AT`).

**Seed 100 check:** round 4 FP fell from 3.0% to 1.7%, and the controller tightened after it.

**G2 dev result (full budget):**
- **FP held:** max 1.56% (mean over seeds). The worst round is 2.0% in round 1, which is the seed rules before learning.
- **Too cautious:** mean FP 0.52%, catch 62%, total cost $97.8k. It loses total cost and both new-attack metrics to both baselines.
- Rounds 4–7 ran at 0–1% FP, so the held-out-fold estimate is conservative on these seeds. The old 60%-of-cap margin had been covering for stale calibration and is no longer needed.

## G3: G2 with the target raised to 85% of the cap; round 2's bootstrap combiner no longer feeds the controller
**G3 dev result:**
- Cost $83.6k, catch 65%, mean FP 0.68%. It still loses cost and both new-attack metrics.
- **Rounds 4–5:** 0% FP and large losses ($14.7k and $15.2k, vs about $8k for the baselines).
- **Rounds 6–10:** costs comparable with rules + XGBoost.
- **Seed 43, round 9:** 2.3% FP.

**Reading:** the calibration estimate is noisy, and early rounds dominate it. The window for rounds 4–5 still includes round 2, where honest traffic shifts as agents gain history. And judging the worst of 3 rounds of about 300 payments each overreacts to noise.

## G4: rounds 1–2 as warm-up, pooled calibration (~900 honest payments), target 75% of the cap, feedback controller kept

**G4 dev result (full budget): wins all 7 target metrics against both baselines; FP held**

| metric | G4 | XGBoost | rules + XGBoost |
|---|---|---|---|
| recall@1% FPR | **0.742** | 0.650 | 0.647 |
| PR-AUC | **0.948** | 0.895 | 0.935 |
| mean FP | **0.88%** | 1.40% | 2.49% |
| max FP (mean over seeds) | **1.67%** | 4.80% | 6.02% |
| total cost | **$69.3k** | $82.1k | $72.7k |
| new attacks, first round | **$14.1k** | $16.6k | $16.1k |
| new attacks, first 3 rounds | **$16.3k** | $21.5k | $20.8k |

- After round 1, every round is at most 1.7%. Seed 43's round 1 is 2.0%: the seed rules, before any learning, identical to static rules.
- Catch (not a target metric) still trails rules + XGBoost: 69% vs 74%.
- G4 became the default. Evaluating on fresh seeds 105–109.

## Fresh-seed evaluation of G4 (seeds 105–109, never used for tuning, full budget)

| metric | ours (G4) | XGBoost | rules + XGBoost |
|---|---|---|---|
| recall@1% FPR | **0.718** | 0.657 | 0.635 |
| PR-AUC | **0.943** | 0.890 | 0.929 |
| mean FP | **0.72%** | 1.32% | 2.30% |
| max FP (mean over seeds) | **2.01%** | 4.42% | 5.29% |
| total cost | $73.5k | $79.2k | **$66.6k** |
| new attacks, first round | $18.1k | $15.1k | **$14.3k** |
| new attacks, first 3 rounds | $20.2k | $17.1k | **$16.3k** |
| catch (not a target) | 68% | 67% | **74%** |
| held-out exam caught | 55% | 17% | **61%** |

### FP cap
- **Rounds 3–10: held on every seed** (max 2.0%). The guard fixed the round-4 breaches (previously 2.3–4.7% on 4 of 5 fresh seeds).
- **Round 2 still breaches on 2 of 5 seeds: 2.3%,** 7 honest payments flagged against the 6 allowed. That's the bootstrap combiner. It's minor, but it counts.
- For comparison, the seed rules alone (static rules) reach 2.3–2.7% on some fresh-seed rounds.

### Metrics
- **Against XGBoost: wins 5 of 7.** It loses both new-attack metrics.
- **Against rules + XGBoost: wins 4 of 7** (recall, PR-AUC, mean FP, max FP). It loses total cost and both new-attack metrics.

### Why the dev win didn't carry over
- **Round 4 is too cautious on these seeds:** $16.5k vs $11.0k for rules + XGBoost. With two warm-up rounds excluded, round 4's calibration rests on round 3 alone, about 300 payments. New attackers count from round 4, so this hits the new-attack metrics directly.
- **The dev margins were within the noise:** total cost, new-attack and catch differences of a few thousand dollars are about the size of the variation between runs.

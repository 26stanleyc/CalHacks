# Head-to-head: our system vs standard baselines

Two benchmarks, both sandboxed:
- **Simulator** (`engine/head_to_head.py`): the agent marketplace, an in-memory store, no real money.
- **PaySim** (`engine/public_bench.py`): a public dataset, read from a local file outside the repo
  (`~/calhacks-sandbox/paysim/`, never committed); nothing is sent anywhere.

Contenders, all scored on the same payments and the same feature vectors:

| contender | what it is |
|---|---|
| `full` / `ours` | our system: specialist rulebooks written and vetted by agents, the learning loop, the XGBoost voter, the arena, threat intel |
| `xgboost_retrained` | XGBoost alone, retrained on all labels so far (bench/ schedule) |
| `xgboost_frozen` / `xgboost_trained` | XGBoost trained on the early rounds or days, then never updated: "trained on yesterday's fraud" |
| `static_rules` | the seed rulebooks, never updated |
| `rules_plus_xgboost` | the typical industry stack: static rules and a retrained XGBoost side by side, the stricter decision wins |

## 1. Simulator, new attack types (mock models, seeds 42-44, 10 rounds)

New attack types are the threat-intel attacker families and the freshly spawned attackers that
first appear after round 3. Columns are fraud cost in their first round and first 3 rounds; total
cost includes what wrongly flagged honest shoppers cost. See [mock.md](mock.md).

| contender | new attacks, first round | total cost | mean FP | max FP |
|---|---|---|---|---|
| full | $25.3k | $126.0k | 0.66% | 2.68% |
| xgboost_retrained | $11.2k | $70.0k | 1.29% | 5.37% |
| xgboost_frozen | $51.0k | $183.2k | 4.60% | 8.36% |
| static_rules | $51.6k | $205.9k | 1.15% | 2.01% |
| rules_plus_xgboost | **$10.9k** | **$59.4k** | 2.37% | 6.38% |

- **Beats** a model trained on yesterday's fraud: about half the losses on new attacks ($25k vs $51k).
  It also beats static rules.
- **Loses** to anything that retrains XGBoost every round. Its total cost is about 2x that of
  `rules_plus_xgboost`, even after counting the honest shoppers the baselines wrongly flag.
- **Why:** our system holds a 2% false-positive cap and calibrates its XGBoost voter to half of it.
  The baselines don't, and their worst rounds flag 5-6% of honest shoppers. Under the cost matrix,
  missed fraud costs far more than friction, so the more aggressive contenders win on cost. With
  the voter calibrated to 2x the cap instead (`VOTER_AT=2.0`), our system matches retrained
  XGBoost on new attacks ($10.3k vs $12.0k) and comes within 11% on total cost ($75.9k vs $68.4k),
  but it then breaks its own cap (max FP 5.35%).

## 2. PaySim public data (10% sample, 278k payments, 821 fraud)

Trained on days 1-15, scored on days 16-31. See [../public-data/paysim.md](../public-data/paysim.md).

| contender | PR-AUC | recall at 1% FPR |
|---|---|---|
| **ours, merged design** (learned combiner + anomaly voter) | **0.102** | **21.2%** |
| ours, before the merge | 0.080 | 16.7% |
| xgboost_retrained | 0.085 | 16.3% |
| rules_plus_xgboost | 0.085 | 16.3% |
| xgboost_trained (frozen) | 0.044 | 13.2% |
| static_rules | 0.008 | 0.0% |

- **With the merged design, ours is clearly ahead of every baseline on public data:** PR-AUC is
  0.102 vs 0.085, and at 1% false positives it catches 21.2% of fraud vs 16.3%, about 30% more. At
  its cost-based operating point it is the only contender that catches any fraud (5.4%, flagging
  0.09% of honest payments).
- Before the merge, it was level with retrained XGBoost (0.080 / 16.7%).
- Absolute detection is low for everyone. PaySim's strong signal is the account balance columns,
  which the dataset's own notes say leak the label, so they're excluded. Our feature set is built
  for agent commerce (mandates, agent baselines, injections), and PaySim exercises none of that.
- The cost columns in paysim.md aren't meaningful. PaySim amounts are in local currency units at
  a very different scale, so every contender's cost-based decision lets almost everything through.

## 3. Simulator with real Bitdeer models (seed 42, 6 rounds)

The first full run on real models: 491 model calls, about 3.1M tokens, 2 answers that didn't parse
(the code fallback decided those), 64 minutes. Compared with the same seed and rounds on mock
models ([bitdeer.md](bitdeer.md), [mock-seed42-6rounds.md](mock-seed42-6rounds.md)):

| contender | new attacks, first round: real / mock | total cost: real / mock | mean FP: real / mock |
|---|---|---|---|
| full | **$3.5k / $16.3k** | **$66.0k / $98.3k** | 0.73% / 0.39% |
| xgboost_retrained | $2.0k / $3.2k | $40.0k / $52.6k | 1.95% / 1.90% |
| rules_plus_xgboost | $1.9k / $3.2k | $30.9k / $43.7k | 2.67% / 2.62% |
| xgboost_frozen | $4.5k / - | $67.9k / - | 4.12% / - |
| static_rules | $8.2k / - | $106.0k / - | 0.73% / - |

- **Real models help our system the most.** Its losses on new attacks fall about 4.6x vs mock, and its
  total cost falls by a third. Behind retrained XGBoost, the gap narrows from 5.1x to 1.7x on new
  attacks and from 1.9x to 1.65x on total cost.
- **The baselines changed too,** because the real-model attackers adapted differently, so the traffic
  differs between the two runs. Compare the ratios more than the dollar amounts.
- **It still loses on cost** to retrained XGBoost and to `rules_plus_xgboost`. The reason is the same
  as on mock: our system holds its 2% FP cap (max 2.01%), and the baselines reach 6.7%.
- **This is one seed.** Real-model runs vary from run to run, so treat it as a first reading, not a result.

## 4. The new design (learned combiner + anomaly voter) on real models (fresh seeds 100-102, 6 rounds)

This is the other session's design: a learned combiner replaces the fixed vote formula, and an anomaly
voter (an Isolation Forest on honest traffic) joins. This run used its S7b version, before the later
"guard" (see `evidence/stacking/strategy-log.md`). It took 1,622 model calls, 10.5M tokens and 18
answers that didn't parse, over 96 minutes for 3 seeds in parallel.
See [bitdeer-stacking.md](bitdeer-stacking.md), and [mock-stacking.md](mock-stacking.md) for the same
seeds on mock models.

| contender | new attacks, first round | total cost | mean FP | max FP |
|---|---|---|---|---|
| **full (new design)** | $11.9k | $58.3k | **1.14%** | **2.67%** |
| xgboost_retrained | $12.3k | $66.4k | 3.47% | 13.0% |
| rules_plus_xgboost | $11.8k | **$55.5k** | 4.23% | 14.0% |
| xgboost_frozen | $21.2k | $104.0k | 7.25% | 16.1% |
| static_rules | $21.6k | $111.2k | 0.78% | 1.67% |

- **Ties the industry stack on new attacks** ($11.9k vs $11.8k; the spread across seeds is large).
- **Beats retrained XGBoost on total cost** and comes within 5% of `rules_plus_xgboost`.
- **Flags about a quarter as many honest shoppers.** On real-model traffic the baselines' worst rounds
  flag 13-14% of honest shoppers. Ours peaks at 2.67%, still slightly over its own 2% cap, the
  breach the later guard version targets.
- 3 seeds with big seed-to-seed variance: a strong first reading, not a final result.

## 5. Recorded demo run: merged design on real models (seed 42, 4 rounds)

In the team database as **`h2h-real-42-202610241623`**; pick it in the dashboard. It took 300 model
calls, 2.0M tokens, 1 answer that didn't parse, and 7.6 minutes a round with parallel calls (about 11
before). See [bitdeer-recorded.md](bitdeer-recorded.md).

| contender | total cost | mean FP | max FP |
|---|---|---|---|
| full (merged design) | $47.0k | **1.67%** | **2.33%** |
| xgboost_retrained | $44.7k | 3.93% | 7.05% |
| rules_plus_xgboost | **$36.6k** | 4.69% | 7.38% |
| static_rules | $78.2k | 0.75% | 2.35% |

The run is short: the learned combiner skips 2 warm-up rounds, and only round 4 has new attackers
(4 of them, so that column isn't meaningful). It's for showing the system at work, not for the
headline numbers; section 4 is the better measurement.

## 6. The 20-round demo run: merged design on real models (seed 42, 20 rounds)

In the team database as **`h2h-real-42-202610241732`**, and exported as the arena's replay.json and
baseline.json. It took 1,401 model calls, 8.8M tokens and 7 answers that didn't parse, at 7.9 minutes a
round. The spawn fix was in: 2 to 4 granted spawns built per round, against 11 of 16 failing before.
See [bitdeer-recorded-20.md](bitdeer-recorded-20.md).

| contender | new attacks, first round | total cost | mean FP | max FP |
|---|---|---|---|---|
| **full (merged design)** | $11.5k | $76.5k | **0.80%** | **2.01%** |
| xgboost_retrained | $11.1k | $81.5k | 1.02% | 7.00% |
| rules_plus_xgboost | **$8.6k** | **$68.2k** | 1.96% | 7.00% |
| xgboost_frozen | $67.8k | $478.9k | 6.73% | 9.33% |
| static_rules | $62.8k | $474.3k | 0.95% | 2.35% |

- **Beats retrained XGBoost on total cost** ($76.5k vs $81.5k) and is level with it on new attacks.
- **About 6x cheaper** than a model trained once or fixed rules over 20 rounds ($76.5k vs about $475k).
  The gap widens the longer attackers adapt.
- **12% behind rules + XGBoost on cost**, while flagging under half as many honest shoppers on
  average (0.80% vs 1.96%). Its worst round is 2.01% against their 7.00%.
- **Per round,** losses fall from $13-14k in rounds 3-4 to under $1k from round 9 on. FP stayed at or
  under 2.01% in every round.

## What this means for the pitch

- Supported: "it adapts to new attacks faster than a model trained on yesterday's fraud," and "real
  models make the agent layer much stronger."
- With the new design on real models: "it matches the standard rules-plus-ML stack on new attacks and
  comes within 5% on cost, while bothering about a quarter as many honest shoppers." That's supported
  by 3 seeds. "It beats it" still isn't.
- The honest framing, on real models: it loses about 1.7x what retrained XGBoost loses on new attacks,
  while flagging about a third as many honest shoppers (0.73% vs 1.95%). Its total cost is still
  higher, because the cost matrix prices fraud far above friction.
- The deciding lever is how much to trust the XGBoost voter (`VOTER_AT`). That's a product decision
  about the false-positive cap, not a bug.

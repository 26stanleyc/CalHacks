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
| ours | 0.080 | **16.7%** |
| xgboost_retrained | **0.085** | 16.3% |
| rules_plus_xgboost | 0.085 | 16.3% |
| xgboost_trained (frozen) | 0.044 | 13.2% |
| static_rules | 0.008 | 0.0% |

- Ours is level with retrained XGBoost and clearly ahead of the frozen model and the static rules.
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

## What this means for the pitch

- Supported: "it adapts to new attacks faster than a model trained on yesterday's fraud," and "real
  models make the agent layer much stronger."
- Not supported yet: "it beats a retrained XGBoost or the standard rules-plus-ML stack." It doesn't
  on cost, though it does keep false positives at or under the cap where they don't.
- The honest framing, on real models: it loses about 1.7x what retrained XGBoost loses on new attacks,
  while flagging about a third as many honest shoppers (0.73% vs 1.95%). Its total cost is still
  higher, because the cost matrix prices fraud far above friction.
- The deciding lever is how much to trust the XGBoost voter (`VOTER_AT`). That's a product decision
  about the false-positive cap, not a bug.

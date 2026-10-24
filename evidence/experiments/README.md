# Engine experiments

Run with `uv run python -m engine.experiments --seeds 42 43 44 --rounds 10 --out evidence/experiments/<name>`.
Each run writes `<name>.json` (per round, per seed) and `<name>.md` (the summary). Mock brains, so
LLM spend is $0. Ryan's precompute runner (R7) owns the official benchmark and its baselines; this
is the engine's own before/after tool.

Setups:
- `full`: the full system as `build_engine` runs it, with the arena, the hijacker, the XGBoost voter and threat intel;
- `outsiders_only`: no arena;
- `no_xgb_voter`: no XGBoost voter;
- `intel_off`: watch rules off, though the intel attackers still arrive.

## 2026-10-24: false positives and the hijacker

[`2026-10-24-baseline.md`](2026-10-24-baseline.md) is before the changes and
[`2026-10-24-after-fixes.md`](2026-10-24-after-fixes.md) is after. Full system, 3 seeds × 10 rounds:

| | before | after |
|---|---|---|
| rounds over the 2% FP cap | 5 of 30 | 0 of 30 |
| max FP | 3.01% | 2.00% |
| mean FP | 0.97% | 0.55% |
| hijacker payments caught | 36% | 41% |
| outsider loss, last round / first | 55% | 55% |
| total cost | $119.9k | $128.3k (± $18.9k across seeds) |

What changed:
1. **A learned rule must look at the payer.** The round-4 spikes came from rules written in round 3 on
   merchant-traffic features only ("6+ new accounts paid this merchant in 10 minutes"). Those have the
   same value for every payer at that merchant, so honest shoppers at a busy or swarmed merchant were
   blocked. A rule (learned, or a watch rule) now needs at least one feature about the payer.
2. **An untrusted voter sits out.** Before it has 3 labeled rounds, or if it can't stay under the target
   false-positive rate even at the floor weight, the XGBoost voter gets weight 0 instead of the 0.05 floor.
   Under the cost matrix even a 5% vote can block a $500 payment. A voter at weight 0 also no longer
   triggers escalation with its own "block" votes. Those escalations had the mock investigator blocking
   honest payments because they resembled the few fraud cases in its memory.
3. **The voter is calibrated on its worst recent round,** not the average of the last two, because drift
   shows up one round at a time.
4. **Threat-intel duplicates are judged by their signals,** whatever the thresholds. A watch rule on the
   same features as a seed rule was stacking on top of it and pushing honest new shoppers into blocks.
5. **A new hijacker signal: today's spend against the agent's usual honest day** (`m_day_spend_vs_agent`).
   A hijacker drains an account in a day. The signal is weak alone (honest shoppers go over 6x their
   norm 1.5% of the time) but helps the XGBoost voter.

Still open:
- `outsiders_only` has 2 rounds over the cap (3.34% max), from XGBoost drift that the weight cut fixes a round late.
- The hijacker is caught 41% of the time vs the 80% target. A device or session signal would need a data-model
  change: Ryan's marketplace treats a new device as a new agent (the travel persona).

# Contract notes

Gaps and questions found while building against `contracts/`. Nothing in `contracts/` was changed; each item is a proposal for the team. Ryan's side (data/, bench/, dashboard/) works around each one as described.

## From Ryan (R1–R5), 2026-10-24

1. **`RoundMetrics` has no field for recall at 1% FPR, PR-AUC or total cost.** Spec 5.1 lists all three as per-defense, per-round metrics.
   - Proposal: add `recall_at_1pct_fpr: float | None`, `pr_auc: float | None` and `total_cost_cents: int = 0` to `RoundMetrics`.
   - For now: `bench/metrics.py` returns them next to `RoundMetrics` in `BenchMetrics`.

2. **No marker for exploitability-exam traffic.** Step 13 of the learning loop runs a frozen exam, but neither `Transaction` nor `db.sql` says which logged rows are exam traffic. They must stay out of the round's loss, FPR and learning labels.
   - Proposal: log exam transactions with `wave = 3` (one past the 3 main waves), or add an `exam boolean` column to `transactions`.
   - For now: `bench/config.py` defines `EXAM_WAVE = 3`. Metrics count waves 0–2 as main traffic and wave 3 as the exam. The seed data follows this, and the dashboard labels those rows "exam".

3. **The freeze state isn't logged anywhere in `db.sql`.** M5 says "the freeze button changes the freeze flag in the database", but there is no such flag.
   - Proposal: the engine writes `RoundMetrics.frozen` for each round, which the dashboard already reads. It could also log freeze and unfreeze events, e.g. a `freeze_events (run_id, round, on, requested_by, at)` table.
   - For now: the dashboard reads the live state from `GET /metrics` (`frozen`), and the curves shade rounds whose logged `round_metrics.payload.frozen` is true.

4. **No read-only route for the freeze state.** `POST /freeze` returns the new state, but the only way to read it is `GET /metrics`.
   - That works and is what the dashboard uses. If the real engine's `get_metrics()` is expensive, a `GET /freeze` would help.

5. **The stub API has no CORS.** The browser can't call `:8000` directly.
   - Not a problem: the dashboard calls the engine from Next.js server actions, server to server, so no change is needed.

6. **Where "uncovered" misses come from.** `RoundMetrics.uncovered_misses` has no source table.
   - For now: metrics count `critic_checks` rows with `kind = 'miss_assignment'` whose `critic->>'lane'` is missing or `'uncovered'`.
   - Stanley: please log uncovered misses that way, or tell me where they'll live.

7. **First-contact catch rate scope.** Spec 5.1 defines it for threat-intel attackers.
   - For now: metrics use attackers with `source = threat_intel` whose first main-traffic payment of the run falls in that round. It could be widened to every new attacker if you want.

8. **Placeholder features.** `data/features.py` builds placeholder feature vectors (field × window × aggregate names) so the XGBoost voter and the baselines can run before I1.
   - At I1, the voter and the baselines should read the engine's `features` table, or a `FeatureService.features_for(tx)` the engine exposes.
   - The voter needs no code change: it trains on whatever keys it is given.

9. **The static-rules baseline uses placeholder rules.**
   - `bench/baselines.py::PLACEHOLDER_RULES` stands in until Stanley's seed rulebooks exist. Swap them in so the baseline is "the seed rulebooks, never updated" (spec 5.1).
   - The placeholder rules flag about 8% of legit traffic on seed 42, mostly the subscription-burst persona.

10. **Seed rows in tables without `run_id`.** `data/seed.py --clear` deletes only rows marked as seed rows:
    - `run_id like 'seed-%'`
    - `mandate_id`, `report_id` and chat `sender` starting with `seed-`
    - merchant page URLs starting with `https://seed-`

    Please don't use those prefixes for real rows.

11. **Mandate ids from the population.** `data.shoppers.Population(seed, id_prefix=...)` builds principal, agent and mandate ids.
    - With the default empty prefix, ids look like `p-0001`, `agent-0001` and `md-0001`. Two runs on the same seed then reuse the same `mandate_id`, which is the primary key of `mandates`.
    - Insert mandates with `on conflict do nothing`, or pass a run-specific prefix.

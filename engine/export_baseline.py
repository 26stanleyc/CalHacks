"""Export a baseline's per-payment decisions on a recorded run, for the arena's "before TagTeam" step.

    uv run python -m engine.export_baseline h2h-real-42-202610241623 --out ../arena-viz/arena/public/baseline.json

The baseline is XGBoost trained once (bench/ XGBoostDefense, frozen after FREEZE_AFTER rounds, as in
engine/head_to_head.py), scoring the same payments with the same logged features. Each round is
decided before that round's labels are learned, so it never sees the round it decides. Step-up
outcomes use the engine's own rule (engine.engine.resolve: a deterministic per-payment draw at the
attacker's pass rate), so a payment's fate matches what the engine would have done.
"""

import argparse
import json
import os
import statistics
from collections import defaultdict
from pathlib import Path

from engine.head_to_head import FREEZE_AFTER


def step_up_pass(source: str, lineage: str | None, costs) -> float:
    """The attacker's chance of getting through a step-up, as the engine sets it per attacker."""
    from engine.arena.hijacker import HIJACKER
    from engine.arena.outsiders import PERSONAS
    from engine.arena.spawning import INSIDER_STEP_UP_PASS
    from engine.intel.watchlist import INTEL_STEP_UP_PASS

    personas = {p.name: p.step_up_pass for p in (*PERSONAS, HIJACKER)}
    if source == "outsider" and lineage in personas:
        return personas[lineage]
    return {"threat_intel": INTEL_STEP_UP_PASS, "spawned": INSIDER_STEP_UP_PASS}.get(source, costs.fraud_step_up_pass)


def export(conn, run_id: str) -> dict:
    from bench.baselines import XGBoostDefense, trainable
    from bench.metrics import cost_cents
    from contracts.schemas import Action, FeatureVector, GroundTruth, Transaction
    from engine.engine import resolve
    from engine.orchestrator import CostMatrix

    costs = CostMatrix()
    rows = lambda sql: conn.execute(sql, (run_id,)).fetchall()
    sources = {aid: (src, (p or {}).get("lineage")) for aid, src, p in
               rows("select distinct on (attacker_id) attacker_id, source, payload from attackers where run_id = %s")}
    txs = rows("select t.payload, f.feature_values, g.is_fraud, g.breaches_mandate, d.action from transactions t "
               "join features f using (tx_id) join decisions d using (tx_id) left join ground_truth g using (tx_id) "
               "where t.run_id = %s order by t.round, (t.payload->>'ts')::float")

    model = XGBoostDefense()
    by_round = defaultdict(list)
    for tx, values, fraud, breach, ours in txs:
        tx = Transaction.model_validate(tx)
        by_round[tx.round].append((tx, FeatureVector(tx_id=tx.tx_id, values=values),
                                   GroundTruth(tx_id=tx.tx_id, is_fraud=bool(fraud), breaches_mandate=bool(breach)),
                                   Action(ours)))
    decisions, totals = {}, {name: defaultdict(float) for name in ("baseline", "ours")}
    fps = {name: [] for name in ("baseline", "ours")}
    for rnd in sorted(by_round):
        flagged = {name: [] for name in fps}
        for tx, features, truth, ours in by_round[rnd]:
            decision = model.decide(tx, features)
            action = decision.action
            src, lineage = sources.get(tx.attacker_id, ("legit", None))
            outcome = resolve(tx, truth, decision, step_up_pass(src, lineage, costs), costs.legit_step_up_abandon)
            decisions[tx.tx_id] = {"action": action.value, "settled": outcome.settled}
            for name, act in (("baseline", action), ("ours", ours)):  # costed as engine/head_to_head.py does
                if truth.is_fraud:
                    totals[name]["fraud_cost_cents"] += cost_cents(True, act, tx.amount_cents)
                elif not truth.breaches_mandate:  # a blocked mandate breach isn't a false positive (CONTRACT_NOTES Q1)
                    totals[name]["honest_cost_cents"] += cost_cents(False, act, tx.amount_cents)
                    flagged[name].append(act != Action.ALLOW)
        for name in fps:
            fps[name].append(statistics.mean(flagged[name]) if flagged[name] else 0.0)
        model.freeze(rnd >= FREEZE_AFTER)  # learns this round's labels only after deciding it
        model.learn(rnd, [(tx, f, g) for tx, f, g, _ in by_round[rnd] if trainable(tx)])

    def summary(name: str) -> dict:
        t = totals[name]
        return {"total_cost_cents": round(t["fraud_cost_cents"] + t["honest_cost_cents"]),
                "fraud_cost_cents": round(t["fraud_cost_cents"]), "honest_cost_cents": round(t["honest_cost_cents"]),
                "mean_fp": round(statistics.mean(fps[name]), 5), "max_fp": round(max(fps[name]), 5)}

    trained = ", ".join(str(r) for r in model.trained_rounds) or "nothing"
    return {"run_id": run_id, "contender": "xgboost_frozen", "label": "XGBoost, trained once",
            "trained_on": f"Fitted on the labels of round(s) {trained} and frozen from round {FREEZE_AFTER}; each round is "
                          f"decided before its labels are learned, so there's no leakage (round 1 is decided untrained).",
            "decisions": decisions, "totals": summary("baseline"), "ours_totals": summary("ours")}


def main() -> None:
    import psycopg

    import integrations.settings  # noqa: F401  (loads .env)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_id")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        out = export(conn, args.run_id)
    args.out.write_text(json.dumps(out, separators=(",", ":")))
    print(f"{len(out['decisions'])} decisions -> {args.out}; baseline {out['totals']}; ours {out['ours_totals']}")
    print(out["trained_on"])


if __name__ == "__main__":
    main()

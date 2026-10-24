"""Public-data benchmark on PaySim (a public mobile-money fraud dataset), run as a sandbox: the data is
read from a local file outside the repo as plain data, results go to a memory store, nothing
is sent anywhere.

PaySim fraud is account takeover: a fraudster empties a victim's account with a TRANSFER to a mule
account, then a CASH_OUT. Only those two types carry fraud, so only they are used. The balance
columns are left out (the dataset's notes say they leak the label, since fraud is cancelled). Each
row becomes a payment: origin account -> agent and principal, destination -> merchant,
step (an hour) -> time, one simulated day per round.

Contenders, all scored on the same feature vectors our feature service computes:
- `ours`: the engine's defense (seed rulebooks, the learning loop and the XGBoost voter), learning
  from each day's labels;
- `xgboost_trained`: XGBoost trained once on the first half of the month, all labels, then frozen:
  the "trained on lots of data" model;
- `xgboost_retrained`: XGBoost retrained on all labels so far, on the benchmark schedule;
- `static_rules`: the seed rulebooks, never updated;
- `rules_plus_xgboost`: the typical industry stack, static rules and the retrained XGBoost side by side,
  the stricter action wins.
Scores are over the second half of the month, which no frozen model saw. Every contender acts by the
same cost matrix (the cheapest action given its fraud probability and the amount), so none wins by a
better-tuned cutoff; PR-AUC compares the scores themselves, independent of any cutoff.

    uv run python -m engine.public_bench --data ~/calhacks-sandbox/paysim/paysim.csv --sample 10 \\
        --out evidence/public-data/paysim
"""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

TYPES = ("TRANSFER", "CASH_OUT")
DAY_STEPS = 24


def load(path: Path, sample_pct: int) -> dict[int, list[tuple]]:
    """Rows by day. A deterministic hash sample keeps the fraud rate as it is."""
    days: dict[int, list[tuple]] = defaultdict(list)
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if row["type"] not in TYPES:
                continue
            key = f"{row['step']}:{row['nameOrig']}:{row['amount']}".encode()
            if int.from_bytes(hashlib.blake2b(key, digest_size=4).digest(), "big") % 100 >= sample_pct:
                continue
            step = int(row["step"])
            days[(step - 1) // DAY_STEPS + 1].append((i, step, row["type"], float(row["amount"]), row["nameOrig"],
                                                      row["nameDest"], row["isFraud"] == "1"))
    return dict(sorted(days.items()))


def run(days: dict[int, list[tuple]], train_days: int) -> dict:
    from bench.baselines import StaticRulesDefense, XGBoostDefense
    from bench.metrics import cost_cents, pr_auc, recall_at_fpr
    from bench.xgb_voter import XGBoostVoter
    from contracts.schemas import Action, GroundTruth, TrafficSource, Transaction
    from engine.brains import Brain
    from engine.learning.labels import Record
    from engine.learning.loop import LearningLoop
    from engine.pipeline import DefensePipeline
    from engine.store import MemoryStore

    pipeline = DefensePipeline(voters=[XGBoostVoter()])
    loop = LearningLoop(pipeline, Brain(), MemoryStore(), "paysim")
    trained, retrained, static = XGBoostDefense(), XGBoostDefense(), StaticRulesDefense()
    history = []
    names = ("ours", "xgboost_trained", "xgboost_retrained", "static_rules", "rules_plus_xgboost")
    strictness = {Action.ALLOW: 0, Action.STEP_UP: 1, Action.BLOCK: 2}
    totals = {name: defaultdict(float) for name in names}
    scored: dict[str, tuple[list, list]] = {name: ([], []) for name in names}  # labels, scores

    def by_cost(defense, tx, features):
        p = float(defense.voter.predict([features.values])[0])
        costs = pipeline.orchestrator.costs.expected_costs(p, tx.amount_cents)
        return min(costs, key=costs.get), p

    for day, rows in days.items():
        records, labeled = [], []
        for i, step, kind, amount, orig, dest, fraud in rows:
            tx = Transaction(tx_id=f"paysim-{i}", run_id="paysim", round=day, ts=step * 3600.0 + (i % 3600),
                             agent_id=orig, principal_id=orig, merchant_id=dest, category=kind.lower(),
                             amount_cents=max(1, round(amount * 100)), source=TrafficSource.LEGIT)
            truth = GroundTruth(tx_id=tx.tx_id, is_fraud=fraud)
            decision, features = pipeline.decide(tx, commit=True)
            records.append(Record(tx, features, decision, truth, settled=decision.action == Action.ALLOW))
            labeled.append((tx, features, truth))
            if day > train_days:  # score the second half only
                fixed = static.decide(tx, features)
                learned = by_cost(retrained, tx, features)
                actions = {"ours": (decision.action, decision.weighted_score), "xgboost_trained": by_cost(trained, tx, features),
                           "xgboost_retrained": learned, "static_rules": (fixed.action, fixed.weighted_score),
                           # ranked by the stricter action, then XGBoost's probability (the rules' score
                           # isn't a probability, so taking the max of the two would let it swamp the ranking)
                           "rules_plus_xgboost": (max(fixed.action, learned[0], key=strictness.get),
                                                  strictness[max(fixed.action, learned[0], key=strictness.get)] + learned[1])}
                for name, (action, score) in actions.items():
                    scored[name][0].append(fraud)
                    scored[name][1].append(score)
                    t = totals[name]
                    cost = cost_cents(fraud, action, tx.amount_cents)
                    if fraud:
                        t["fraud"] += 1
                        t["caught"] += action != Action.ALLOW
                        t["fraud_cost"] += cost
                    else:
                        t["honest"] += 1
                        t["flagged"] += action != Action.ALLOW
                        t["honest_cost"] += cost
        loop.learn(day, records)
        history.extend(labeled)
        retrained.learn(day, labeled)
        if day == train_days:  # one fit on everything in the first half, then frozen
            trained.voter.retrain([(fv, g.is_fraud) for _, fv, g in history])
            trained.freeze(True)
    return {name: {"recall": t["caught"] / max(1, t["fraud"]), "fp_rate": t["flagged"] / max(1, t["honest"]),
                   "fraud_cost_usd": t["fraud_cost"] / 100, "honest_cost_usd": t["honest_cost"] / 100,
                   "total_cost_usd": (t["fraud_cost"] + t["honest_cost"]) / 100, "fraud": int(t["fraud"]),
                   "honest": int(t["honest"]), "pr_auc": pr_auc(*scored[name]),
                   "recall_at_1pct_fpr": recall_at_fpr(*scored[name], 0.01)} for name, t in totals.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--sample", type=int, default=10, help="percent of TRANSFER/CASH_OUT rows to keep")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    days = load(args.data.expanduser(), args.sample)
    train_days = len(days) // 2
    results = run(days, train_days)
    n = sum(len(r) for r in days.values())
    fraud = sum(row[-1] for r in days.values() for row in r)
    lines = [f"PaySim, TRANSFER and CASH_OUT, {args.sample}% sample: {n:,} payments ({fraud:,} fraud) over {len(days)} days. "
             f"Trained on days 1-{train_days}, scored on days {train_days + 1}-{max(days)}.", "",
             "| contender | PR-AUC | recall at 1% FPR | fraud caught | honest flagged | missed-fraud cost | honest-shopper cost | total cost |",
             "|---|---|---|---|---|---|---|---|"]
    for name, r in results.items():
        auc = f"{r['pr_auc']:.3f}" if r["pr_auc"] is not None else "-"
        rec = f"{r['recall_at_1pct_fpr']:.1%}" if r["recall_at_1pct_fpr"] is not None else "-"
        lines.append(f"| {name} | {auc} | {rec} | {r['recall']:.1%} | {r['fp_rate']:.2%} | ${r['fraud_cost_usd']:,.0f} | "
                     f"${r['honest_cost_usd']:,.0f} | ${r['total_cost_usd']:,.0f} |")
    table = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps({"sample_pct": args.sample, "results": results}, indent=1))
    args.out.with_suffix(".md").write_text(table)
    print(table)


if __name__ == "__main__":
    main()

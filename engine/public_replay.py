"""Run a public fraud dataset through TagTeam and the trained-once XGBoost baseline, and export both in
the arena's formats (arena/src/data/types.ts), as the demo's second data set next to the simulated
marketplace. A sandbox: the data is read from a local file outside the repo, results stay in memory,
nothing is sent anywhere.

Dataset: ULB / Worldline credit-card fraud (Dal Pozzolo et al., IEEE CIDM 2015; Kaggle mlg-ulb/creditcardfraud):
284,807 real card payments by European cardholders over two days of September 2013, 492 labelled fraud.
Licensed ODbL 1.0 (database) and DbCL 1.0 (contents); the exported files are derived from it and say so.

How its columns map to our feature service (and what had to be approximated):
- Amount (EUR) -> amount_cents, in euro cents. The arena shows euros.
- Time (seconds since the dataset's first payment) -> ts. Rounds are equal slices of the two days, each
  split into three waves by time.
- The data has no card, customer or merchant ids, so every payment gets its own agent, principal and
  merchant: each payer is new to the per-account features (velocity, graph, identity, mandate), and the
  merchant-traffic features see one payment each. Those lanes have almost nothing to go on.
- V1..V28 (a PCA of the bank's confidential features) -> extra features x_v1..x_v28 outside every lane:
  the XGBoost and anomaly voters (TagTeam's and the baseline's) use them; the rule-writing specialists
  don't, since the components have no meaning to write a rule about.
- A step-up's outcome isn't in the data, so it uses the engine's simulated rule (engine.engine.resolve:
  a deterministic per-payment draw at the cost matrix's pass and abandon rates), for both contenders.
- Specialists reflect with the engine's mock brains (deterministic, no LLM calls), and there's no LLM
  investigator: the replay's defenders say "mock-reflection".

Fraud in the data isn't grouped by who committed it, so the arena shows it as three groups by amount
(source "dataset"), each standing on its own tile. Allowed honest payments carry an empty explanation
(the arena doesn't show it), and baseline.json lists only the decisions that aren't "allow, settled",
which is what the arena assumes for a payment it doesn't find there.

    uv run python -m engine.public_replay --data ~/calhacks-sandbox/ulb/creditcard.csv \\
        --out ../arena-real-data/arena/public/real
"""

import argparse
import csv
import gzip
import json
import statistics
from collections import defaultdict
from pathlib import Path

DAY_SECONDS = 86_400
WAVES = 3
V_COLUMNS = [f"V{k}" for k in range(1, 29)]
BANDS = (("under_10", 1_000), ("10_to_100", 10_000), ("over_100", None))  # cents, as the arena's shelves
BAND_NAME = {"under_10": "under €10", "10_to_100": "€10 to €100", "over_100": "over €100"}
DATASET = {
    "name": "ULB / Worldline credit-card fraud",
    "source": "284,807 real card payments by European cardholders, September 2013 (Université Libre de Bruxelles "
              "and Worldline; Kaggle: Credit Card Fraud Detection)",
    "url": "https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud",
    "license": "ODbL 1.0 / DbCL 1.0",
    "currency": "EUR",
}


def band(amount_cents: int) -> str:
    return next(name for name, below in BANDS if below is None or amount_cents < below)


def load(path: Path) -> list[tuple]:
    """(row, seconds, amount in cents, {x_v1: ..}, is_fraud) in time order."""
    rows = []
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            extra = {f"x_{c.lower()}": float(row[c]) for c in V_COLUMNS}
            rows.append((i, float(row["Time"]), round(float(row["Amount"]) * 100), extra, row["Class"].strip('"') == "1"))
    return sorted(rows, key=lambda r: (r[1], r[0]))


def run(rows: list[tuple], n_rounds: int, run_id: str) -> tuple[dict, dict]:
    """TagTeam and the baseline on the same payments, round by round. Returns (replay, baseline)."""
    from bench.baselines import XGBoostDefense
    from bench.metrics import cost_cents
    from contracts.schemas import Action, FeatureVector, GroundTruth, TrafficSource, Transaction
    from engine.brains import Brain
    from engine.engine import resolve
    from engine.entrypoints import _voters
    from engine.export_replay import LANES, build
    from engine.learning.labels import Record
    from engine.learning.loop import LearningLoop
    from engine.pipeline import DefensePipeline
    from engine.store import MemoryStore

    span = rows[-1][1] + 1
    round_s = span / n_rounds
    store = MemoryStore()
    pipeline = DefensePipeline(voters=_voters())
    loop = LearningLoop(pipeline, Brain(), store, run_id)
    costs = pipeline.orchestrator.costs
    baseline = XGBoostDefense()

    by_round: dict[int, list] = defaultdict(list)
    for i, seconds, cents, extra, fraud in rows:
        rnd = min(n_rounds, int(seconds // round_s) + 1)
        wave = min(WAVES, int((seconds - (rnd - 1) * round_s) // (round_s / WAVES)) + 1)
        tx = Transaction(tx_id=f"u{i}", run_id=run_id, round=rnd, wave=wave, ts=seconds, agent_id=f"card-{i}",
                         principal_id=f"card-{i}", merchant_id=f"merchant-{i}", category="card",
                         amount_cents=cents, source=TrafficSource.LEGIT)
        by_round[rnd].append((tx, extra, GroundTruth(tx_id=tx.tx_id, is_fraud=fraud)))

    t = {"seed": 0, "defense": "full", "metrics": [], "reports": [], "attackers": [], "actions": [], "votes": [],
         "payments": [], "waves": [], "snapshots": [], "versions": []}
    decisions = {}  # the baseline's, by tx_id
    totals = {name: defaultdict(float) for name in ("baseline", "ours")}
    fps = {name: [] for name in totals}
    for rnd in sorted(by_round):
        records, labeled = [], []
        flagged = {name: [] for name in totals}
        stolen_by_band: dict[str, int] = defaultdict(int)
        tried_by_band: dict[str, list] = defaultdict(lambda: [0, 0])  # attempts, caught
        for tx, extra, truth in by_round[rnd]:
            base = pipeline.featurize(tx, commit=True)
            features = FeatureVector(tx_id=tx.tx_id, values={**base.values, **extra})
            ours = pipeline.score(tx, features)
            settled = resolve(tx, truth, ours, costs.fraud_step_up_pass, costs.legit_step_up_abandon).settled
            records.append(Record(tx, features, ours, truth, settled=settled))
            labeled.append((tx, features, truth))

            theirs = baseline.decide(tx, features)
            their_settled = resolve(tx, truth, theirs, costs.fraud_step_up_pass, costs.legit_step_up_abandon).settled
            if theirs.action != Action.ALLOW or not their_settled:
                decisions[tx.tx_id] = {"action": theirs.action.value, "settled": their_settled}

            for name, action in (("baseline", theirs.action), ("ours", ours.action)):  # as engine/export_baseline.py
                if truth.is_fraud:
                    totals[name]["fraud_cost_cents"] += cost_cents(True, action, tx.amount_cents)
                else:
                    totals[name]["honest_cost_cents"] += cost_cents(False, action, tx.amount_cents)
                    flagged[name].append(action != Action.ALLOW)

            group = f"ulb-fraud-{band(tx.amount_cents)}" if truth.is_fraud else None
            if group:
                tried_by_band[group][0] += 1
                tried_by_band[group][1] += not settled
                stolen_by_band[group] += tx.amount_cents if settled else 0
            for v in ours.votes:
                if v.voter in LANES:
                    t["votes"].append((tx.tx_id, v.voter, {"vote": v.vote.value}))
            t["payments"].append((tx.tx_id, rnd, tx.wave, "dataset" if truth.is_fraud else "legit", group,
                                  {"amount_cents": tx.amount_cents}, ours.action.value, False,
                                  {"explanation": "" if ours.action == Action.ALLOW and not truth.is_fraud else ours.explanation},
                                  truth.is_fraud, settled))

        for name in fps:
            fps[name].append(statistics.mean(flagged[name]) if flagged[name] else 0.0)
        honest = [r for r in records if not r.truth.is_fraud]
        t["metrics"].append((rnd, {
            "loss_merchant_side_cents": 0, "loss_buyer_side_cents": sum(stolen_by_band.values()),
            "false_positive_rate": sum(r.decision.action != Action.ALLOW for r in honest) / max(1, len(honest)),
            "exploitability_cents": -1,  # not measured on a dataset: there's no attacker to re-run
            "shelves_defended": 0, "archive_coverage": 0, "frozen": False}))
        for group, (attempts, caught) in tried_by_band.items():
            amount = group.removeprefix("ulb-fraud-")
            t["attackers"].append((rnd, {
                "attacker_id": group, "name": f"Card fraud {BAND_NAME[amount]}", "source": "dataset",
                "playbook": {"shelf": {"style": "dataset", "amount": amount, "side": "buyer"},
                             "strategy": f"Every payment the dataset labels as fraud {BAND_NAME[amount]}. The data doesn't "
                                         "say who made them or how, so they're grouped by amount only.", "params": {}},
                "profit_cents": 0}))
            t["snapshots"].append((rnd, f"dataset/{amount}/buyer", {"attacker_ids": [group], "outsider_slot": None,
                                                                   "attempts": attempts, "caught": caught}))

        loop.learn(rnd, records)
        baseline.freeze(rnd >= 2)  # one fit, on round 1's labels; it decides each round before learning it
        baseline.learn(rnd, labeled)
        print(f"round {rnd}: {len(records):,} payments, {sum(r.truth.is_fraud for r in records)} fraud", flush=True)

    t["versions"] = [(r["round"], r["lane"], r["rules"], r.get("diff")) for r in store.rows["rulebook_versions"]
                     if r.get("deployed", True)]
    replay = build(t, run_id)
    for d in replay["defenders"]:
        d["model"] = "engine/mock-reflection"
    replay["dataset"] = {**DATASET, "round_hours": round(round_s / 3600, 2)}

    def summary(name: str) -> dict:
        s = totals[name]
        return {"total_cost_cents": round(s["fraud_cost_cents"] + s["honest_cost_cents"]),
                "fraud_cost_cents": round(s["fraud_cost_cents"]), "honest_cost_cents": round(s["honest_cost_cents"]),
                "mean_fp": round(statistics.mean(fps[name]), 5), "max_fp": round(max(fps[name]), 5)}

    trained = ", ".join(str(r) for r in baseline.trained_rounds) or "nothing"
    out = {"run_id": run_id, "contender": "xgboost_frozen", "label": "XGBoost, trained once",
           "trained_on": f"Fitted on the labels of round(s) {trained} and frozen from round 2; each round is decided "
                         f"before its labels are learned, so there's no leakage (round 1 is decided untrained).",
           "omitted": "Decisions not listed here are allow, settled.",
           "decisions": decisions, "totals": summary("baseline"), "ours_totals": summary("ours")}
    return replay, out


def check(replay: dict, baseline: dict) -> dict:
    """The arena's step-0 numbers, computed straight from the two files (arena/src/data/baseline.ts flowByRound
    and the scoreboard's flagged count): net = honest money through minus money stolen."""
    out = {}
    for stage in ("baseline", "tagteam"):
        honest = stolen = flagged = 0
        for r in replay["rounds"]:
            for w in r["waves"]:
                for p in w["payments"]:
                    d = baseline["decisions"].get(p["tx_id"], {"action": "allow", "settled": True}) if stage == "baseline" else p
                    if not p["is_fraud"] and d["action"] != "allow":
                        flagged += 1
                    if d["settled"]:
                        stolen += p["amount_cents"] if p["is_fraud"] else 0
                        honest += 0 if p["is_fraud"] else p["amount_cents"]
        out[stage] = {"honest_through_cents": honest, "stolen_cents": stolen, "net_cents": honest - stolen,
                      "honest_flagged": flagged}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="creditcard.csv")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--out", type=Path, required=True, help="a directory: replay.json.gz and baseline.json go there")
    args = parser.parse_args()

    rows = load(args.data.expanduser())
    run_id = f"ulb-creditcard-{args.rounds}r"
    replay, baseline = run(rows, args.rounds, run_id)
    args.out.mkdir(parents=True, exist_ok=True)
    # Every payment is in the replay (the arena's scoreboard counts them all), so it's gzipped; the arena unzips it.
    (args.out / "replay.json.gz").write_bytes(gzip.compress(json.dumps(replay, separators=(",", ":"), ensure_ascii=False).encode(), 9, mtime=0))
    (args.out / "baseline.json").write_text(json.dumps(baseline, separators=(",", ":"), ensure_ascii=False))
    print(json.dumps(check(replay, baseline), indent=1))
    print(f"baseline {baseline['totals']}\nours {baseline['ours_totals']}\n{baseline['trained_on']}")


if __name__ == "__main__":
    main()

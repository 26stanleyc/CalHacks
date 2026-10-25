"""Run a public fraud dataset through TagTeam and the trained-once XGBoost baseline, and export both in
the arena's formats (arena/src/data/types.ts), as extra data sets next to the simulated marketplace.
A sandbox: the data is read from local files outside the repo, results stay in memory, nothing is sent
anywhere.

Two datasets:

ethereum: XBlock's Ethereum phishing transaction network (Chen et al., "Phishing scams detection in
Ethereum transaction network", ACM TOIT 2021; xblock.pro, Kaggle xblock/ethereum-phishing-transaction-network).
Real Ethereum transactions, crawled two hops out from 1,165 addresses Etherscan labels as phishing, so it's
traffic around known scams, not a random sample of Ethereum. Convert MulDiGraph.pkl once with
engine/eth_phishing.py (a safe loader), then pass the converted folder here.
- sender -> agent and principal, receiver -> merchant, ETH moved -> amount_cents in hundredths of an ETH
  (the arena shows Ξ), timestamp -> ts. Each round is a slice of a time window, three waves to a round.
- fraud = a payment into a phishing address (a victim paying a scammer). Payments sent by phishing
  addresses are left out: they move stolen money on, they don't steal it, and they aren't honest either.
- transfers under Ξ0.005 round to zero hundredths and are left out, as are zero-value transactions
  (contract calls that move no ETH): they aren't payments.
- the window: the run picks the stretch of --days days with the most phishing payments that stays under
  --max-payments payments in all (or --start), since the 13.5M transactions are far more than a replay holds.
- no extra features: both contenders see only what our feature service computes.

ulb: ULB / Worldline credit-card fraud (Dal Pozzolo et al., IEEE CIDM 2015; Kaggle mlg-ulb/creditcardfraud).
284,807 real card payments by European cardholders over two days of September 2013, 492 labelled fraud.
ODbL 1.0 (database) and DbCL 1.0 (contents); the exported files are derived from it and say so.
- Amount (EUR) -> amount_cents in euro cents; Time (seconds since the first payment) -> ts.
- No card, customer or merchant ids, so every payment gets its own agent, principal and merchant: the
  per-account features (velocity, graph, identity, mandate) have almost nothing to go on.
- V1..V28 (a PCA of the bank's confidential features) -> extra features x_v1..x_v28 outside every lane:
  the XGBoost and anomaly voters (TagTeam's and the baseline's) use them; the rule-writing specialists don't.

For both:
- A step-up's outcome isn't in the data, so it uses the engine's simulated rule (engine.engine.resolve: a
  deterministic per-payment draw at the cost matrix's pass and abandon rates), for both contenders.
- Specialists reflect with the engine's mock brains (deterministic, no LLM calls; the replay's defenders
  say "mock-reflection"), or with the Bitdeer models from .env when --real is given. No LLM investigator.
- The arena shows the fraud as three groups by amount (source "dataset"). Allowed honest payments carry
  an empty explanation (the arena doesn't show it), and baseline.json lists only the decisions that
  aren't "allow, settled", which is what the arena assumes for a payment it doesn't find there.

    uv run python -m engine.public_replay ethereum --data ~/calhacks-sandbox/eth --out ../arena-real-data/arena/public/eth
    uv run python -m engine.public_replay ulb --data ~/calhacks-sandbox/ulb/creditcard.csv --out ../arena-real-data/arena/public/real
"""

import argparse
import csv
import gzip
import json
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DAY_SECONDS = 86_400
WAVES = 3
BURN_IN_CAP = 0.02  # the learning loop's false-alarm budget (LearningLoop fp_cap): a rule firing on more is off
V_COLUMNS = [f"V{k}" for k in range(1, 29)]
BANDS = (("under_10", 1_000), ("10_to_100", 10_000), ("over_100", None))  # cents, as the arena's shelves
DATASETS = {
    "ulb": {
        "name": "ULB / Worldline credit-card fraud",
        "source": "284,807 real card payments by European cardholders, September 2013 (Université Libre de Bruxelles "
                  "and Worldline; Kaggle: Credit Card Fraud Detection)",
        "url": "https://www.kaggle.com/datasets/mlg-ulb/creditcardfraud",
        "license": "ODbL 1.0 / DbCL 1.0",
        "currency": "EUR",
        "fraud": "Card fraud",
        "what": "Every payment the dataset labels as fraud {band}. The data doesn't say who made them or how, so "
                "they're grouped by amount only.",
    },
    "ethereum": {
        "name": "XBlock Ethereum phishing network",
        "source": "Real Ethereum transactions around 1,165 addresses Etherscan labels as phishing (XBlock, Sun Yat-sen "
                  "University; Chen et al., ACM TOIT 2021), crawled outward from those scams, so it isn't a random sample",
        "url": "https://xblock.pro/#/dataset/13",
        "license": "no license stated",
        "currency": "ETH",
        "fraud": "Payments to phishing addresses",
        "what": "Every payment {band} into an address Etherscan labels as phishing: a victim paying a scammer. Grouped "
                "by amount; the scammers themselves aren't drawn one by one.",
    },
}
SYMBOL = {"EUR": "€", "ETH": "Ξ"}


def band(amount_cents: int) -> str:
    return next(name for name, below in BANDS if below is None or amount_cents < below)


def band_name(name: str, currency: str) -> str:
    s = SYMBOL[currency]
    return {"under_10": f"under {s}10", "10_to_100": f"{s}10 to {s}100", "over_100": f"over {s}100"}[name]


# A payment row: (tx_id, ts, amount_cents, payer, payee, extra features, is_fraud), in time order. Each loader
# returns (rows, start, seconds, dated, burn_in): the span the rounds divide, whether ts is a real date
# (unix time), and the payments just before the window for the shadow burn-in (their labels are never read).

def load_ulb(path: Path) -> tuple[list[tuple], float, float, bool]:
    rows = []
    with path.open(newline="") as f:
        for i, row in enumerate(csv.DictReader(f)):
            extra = {f"x_{c.lower()}": float(row[c]) for c in V_COLUMNS}
            rows.append((f"u{i}", float(row["Time"]), round(float(row["Amount"]) * 100), f"card-{i}", f"merchant-{i}",
                         extra, row["Class"].strip('"') == "1"))
    rows.sort(key=lambda r: (r[1], r[0]))
    return rows, 0.0, rows[-1][1] + 1, False, []  # Time counts seconds from the first payment: no date, nothing before


def load_ethereum(folder: Path, days: int, max_payments: int, start: str | None,
                  before_days: int = 0) -> tuple[list[tuple], float, float, bool, list[tuple]]:
    """The window of `days` days with the most payments into phishing addresses, at most `max_payments` in all."""
    import numpy as np

    phishing = np.fromfile(folder / "phishing.u8", dtype=np.uint8).astype(bool)
    src = np.fromfile(folder / "src.u32", dtype=np.uint32)
    dst = np.fromfile(folder / "dst.u32", dtype=np.uint32)
    amount = np.fromfile(folder / "amount.f64", dtype=np.float64)
    ts = np.fromfile(folder / "ts.f64", dtype=np.float64)
    cents = np.round(amount * 100).astype(np.int64)
    keep = (cents > 0) & ~phishing[src]  # payments only; a scammer moving stolen money on is neither theft nor honest
    fraud = phishing[dst]
    day = (ts // DAY_SECONDS).astype(np.int64)
    first = int(day[keep].min())
    per_day = np.bincount(day[keep] - first)
    fraud_per_day = np.bincount(day[keep & fraud] - first, minlength=len(per_day))
    print(f"{int(keep.sum()):,} payments ({int((keep & fraud).sum()):,} to phishing addresses) over {len(per_day):,} days; "
          f"left out: {int((cents == 0).sum()):,} under Ξ0.005, {int(((cents > 0) & phishing[src]).sum()):,} sent by phishing addresses")
    if start:
        lo = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() // DAY_SECONDS) - first
    else:
        totals = np.convolve(per_day, np.ones(days, dtype=np.int64), "valid")
        frauds = np.convolve(fraud_per_day, np.ones(days, dtype=np.int64), "valid")
        frauds[totals > max_payments] = -1
        lo = int(frauds.argmax())
    when = lambda d: datetime.fromtimestamp((first + d) * DAY_SECONDS, timezone.utc).date()
    print(f"window {when(lo)} to {when(lo + days - 1)}: {int(per_day[lo:lo + days].sum()):,} payments, "
          f"{int(fraud_per_day[lo:lo + days].sum()):,} to phishing addresses")
    addresses = (folder / "addresses.txt").read_text().split("\n")

    def rows_between(a: int, b: int) -> list[tuple]:
        picked = np.flatnonzero(keep & (day >= first + a) & (day < first + b))
        picked = picked[np.lexsort((picked, ts[picked]))]
        return [(f"e{i}", float(ts[i]), int(cents[i]), addresses[src[i]], addresses[dst[i]], {}, bool(fraud[i])) for i in picked]

    before = rows_between(lo - before_days, lo) if before_days else []  # for a burn-in or a training period
    return rows_between(lo, lo + days), float((first + lo) * DAY_SECONDS), float(days * DAY_SECONDS), True, before


def run(rows: list[tuple], t0: float, span: float, dated: bool, n_rounds: int, run_id: str, meta: dict,
        real: bool = False, burn: list[tuple] = (), train: list[tuple] = ()) -> tuple[dict, dict]:
    """TagTeam and the baseline on the same payments, round by round: rounds split [t0, t0 + span) evenly.
    `burn`: payments before the window for a shadow burn-in. They warm up the shared feature history (both
    contenders score from it) and TagTeam switches off any seed rule that would flag more than BURN_IN_CAP
    of them; nothing is decided and no label is read.
    `train`: labelled payments before the window, a training period. TagTeam learns from them one day at a
    time as it would live (rules, voters, combiner); the baseline is fitted once on all of them and frozen,
    so it decides round 1 trained. Neither sees a test payment's label before deciding it.
    Returns (replay, baseline)."""
    from bench.baselines import XGBoostDefense
    from bench.metrics import cost_cents
    from contracts.schemas import Action, FeatureVector, GroundTruth, TrafficSource, Transaction
    from engine.brains import Brain
    from engine.engine import resolve
    from contracts.config import model_for
    from engine.entrypoints import _brain, _voters
    from engine.export_replay import LANES, build
    from engine.learning.labels import Record
    from engine.learning.loop import LearningLoop
    from engine.pipeline import DefensePipeline
    from engine.store import MemoryStore

    round_s = span / n_rounds
    store = MemoryStore()
    pipeline = DefensePipeline(voters=_voters())
    burned = shadow_burn_in(pipeline, burn, run_id) if burn else {}
    brain = _brain() if real else Brain()  # real: the Bitdeer models from .env reflect and write rules
    loop = LearningLoop(pipeline, brain, store, run_id)
    costs = pipeline.orchestrator.costs
    baseline = XGBoostDefense()
    trained_days = train_period(pipeline, loop, baseline, train, run_id, costs) if train else []
    offset = len(trained_days)  # the learning loop counts training days as its first rounds

    by_round: dict[int, list] = defaultdict(list)
    for tx_id, ts, cents, payer, payee, extra, fraud in rows:
        rnd = min(n_rounds, int((ts - t0) // round_s) + 1)
        wave = min(WAVES, int((ts - t0 - (rnd - 1) * round_s) // (round_s / WAVES)) + 1)
        tx = Transaction(tx_id=tx_id, run_id=run_id, round=rnd, wave=wave, ts=ts, agent_id=payer, principal_id=payer,
                         merchant_id=payee, category="payment", amount_cents=cents, source=TrafficSource.LEGIT)
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
            features = FeatureVector(tx_id=tx.tx_id, values={**base.values, **extra}) if extra else base
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

            group = f"dataset-fraud-{band(tx.amount_cents)}" if truth.is_fraud else None
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
            amount = group.removeprefix("dataset-fraud-")
            named = band_name(amount, meta["currency"])
            t["attackers"].append((rnd, {
                "attacker_id": group, "name": f"{meta['fraud']} {named}", "source": "dataset",
                "playbook": {"shelf": {"style": "dataset", "amount": amount, "side": "buyer"},
                             "strategy": meta["what"].format(band=named), "params": {}},
                "profit_cents": 0}))
            t["snapshots"].append((rnd, f"dataset/{amount}/buyer", {"attacker_ids": [group], "outsider_slot": None,
                                                                   "attempts": attempts, "caught": caught}))

        loop.learn(rnd + offset, records)
        if not trained_days:
            baseline.freeze(rnd >= 2)  # one fit, on round 1's labels; it decides each round before learning it
            baseline.learn(rnd, labeled)
        print(f"round {rnd}: {len(records):,} payments, {sum(r.truth.is_fraud for r in records)} fraud", flush=True)

    # Rules the burn-in switched off, or the training period left, are in place from round 1 on, before the
    # round's own learning; test rounds' versions follow at their own round.
    deployed = [r for r in store.rows["rulebook_versions"] if r.get("deployed", True)]
    after_training = {r["lane"]: r["rules"] for r in deployed if r["round"] <= offset}
    t["versions"] = ([(1, lane, rules, {"retired": off}) for lane, (rules, off) in burned.items()]
                     + [(1, lane, rules, {}) for lane, rules in after_training.items()]
                     + [(r["round"] - offset, r["lane"], r["rules"], r.get("diff")) for r in deployed if r["round"] > offset])
    replay = build(t, run_id)
    for d in replay["defenders"]:
        d["model"] = model_for(d["lane"]) if real else "engine/mock-reflection"
    if real and brain.failures:
        print(f"{len(brain.failures)} model replies were refused or invalid and fell back to the mock", flush=True)
    replay["dataset"] = {k: meta[k] for k in ("name", "source", "url", "license", "currency")} | {
        "round_hours": round(round_s / 3600, 2),
        "start": datetime.fromtimestamp(t0, timezone.utc).isoformat() if dated else None}

    def summary(name: str) -> dict:
        s = totals[name]
        return {"total_cost_cents": round(s["fraud_cost_cents"] + s["honest_cost_cents"]),
                "fraud_cost_cents": round(s["fraud_cost_cents"]), "honest_cost_cents": round(s["honest_cost_cents"]),
                "mean_fp": round(statistics.mean(fps[name]), 5), "max_fp": round(max(fps[name]), 5)}

    if trained_days:
        trained_on = (f"Fitted once on all labels of the {len(trained_days)} days before the test window ({trained_days[0]} to "
                      f"{trained_days[-1]}, {len(train):,} payments) and frozen; TagTeam learned from the same days, one at a time.")
    else:
        trained = ", ".join(str(r) for r in baseline.trained_rounds) or "nothing"
        trained_on = (f"Fitted on the labels of round(s) {trained} and frozen from round 2; each round is decided before its "
                      f"labels are learned, so there's no leakage (round 1 is decided untrained).")
    out = {"run_id": run_id, "contender": "xgboost_frozen", "label": "XGBoost, trained once", "trained_on": trained_on,
           "pretrained": bool(trained_days),
           "omitted": "Decisions not listed here are allow, settled.",
           "decisions": decisions, "totals": summary("baseline"), "ours_totals": summary("ours")}
    return replay, out


def train_period(pipeline, loop, baseline, train: list[tuple], run_id: str, costs) -> list[str]:
    """TagTeam learns from the training payments a day at a time, deciding each day before learning its labels,
    as it would live. The baseline is then fitted once on all of them and frozen. Returns the days (ISO dates)."""
    from contracts.schemas import GroundTruth, TrafficSource, Transaction
    from engine.engine import resolve
    from engine.learning.labels import Record

    days: dict[int, list] = defaultdict(list)
    for row in train:
        days[int(row[1] // DAY_SECONDS)].append(row)
    examples = []
    for k, day in enumerate(sorted(days), 1):
        records = []
        for tx_id, ts, cents, payer, payee, extra, fraud in days[day]:
            tx = Transaction(tx_id=tx_id, run_id=run_id, round=k, ts=ts, agent_id=payer, principal_id=payer, merchant_id=payee,
                             category="payment", amount_cents=cents, source=TrafficSource.LEGIT)
            truth = GroundTruth(tx_id=tx_id, is_fraud=fraud)
            features = pipeline.featurize(tx, commit=True)
            decision = pipeline.score(tx, features)
            settled = resolve(tx, truth, decision, costs.fraud_step_up_pass, costs.legit_step_up_abandon).settled
            records.append(Record(tx, features, decision, truth, settled=settled))
            examples.append((features, fraud))
        loop.learn(k, records)
        print(f"training day {k}: {len(records):,} payments, {sum(r.truth.is_fraud for r in records)} fraud", flush=True)
    baseline.voter.retrain(examples)
    baseline.freeze(True)
    return [datetime.fromtimestamp(d * DAY_SECONDS, timezone.utc).date().isoformat() for d in sorted(days)]


def shadow_burn_in(pipeline, burn: list[tuple], run_id: str) -> dict:
    """Score the burn-in payments without acting on them and switch off every rule that fires on more than
    BURN_IN_CAP of them. Label-free: only how often a rule fires on ordinary traffic. Returns
    {lane: (rules left, ids switched off)} for each lane that changed."""
    from collections import Counter

    from contracts.schemas import TrafficSource, Transaction
    from engine.specialists import Specialist

    fired: Counter = Counter()
    for tx_id, ts, cents, payer, payee, extra, _ in burn:  # the label is never read
        tx = Transaction(tx_id=tx_id, run_id=run_id, round=0, ts=ts, agent_id=payer, principal_id=payer, merchant_id=payee,
                         category="payment", amount_cents=cents, source=TrafficSource.LEGIT)
        features = pipeline.featurize(tx, commit=True)
        for lane in pipeline.lanes:
            fired.update(pipeline.vote(lane, tx, features).rule_ids)
    changed = {}
    for lane, specialist in pipeline.specialists.items():
        book = specialist.rulebook
        off = [r.id for r in book.rules if fired[r.id] / len(burn) > BURN_IN_CAP]
        if off:
            rules = [r for r in book.rules if r.id not in off]
            pipeline.specialists[lane] = Specialist(lane, book.model_copy(update={"rules": rules, "version": book.version + 1}),
                                                    pipeline.catalog)
            changed[lane.value] = ([r.model_dump(mode="json") for r in rules], off)
    print(f"burn-in: {len(burn):,} payments; share of payments each rule fires on: "
          f"{ {r: f'{n / len(burn):.1%}' for r, n in fired.most_common(8)} }; switched off (over {BURN_IN_CAP:.0%}): {[i for _, o in changed.values() for i in o]}",
          flush=True)
    return changed


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
    parser.add_argument("dataset", choices=sorted(DATASETS))
    parser.add_argument("--data", type=Path, required=True, help="ulb: creditcard.csv; ethereum: engine/eth_phishing.py's output folder")
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--days", type=int, default=6, help="ethereum: the window's length in days")
    parser.add_argument("--max-payments", type=int, default=300_000, help="ethereum: the most payments the window may hold")
    parser.add_argument("--start", help="ethereum: the window's first day (YYYY-MM-DD) instead of picking one")
    parser.add_argument("--real", action="store_true", help="reflect with the Bitdeer models (.env) instead of the mock brains")
    parser.add_argument("--burn-in-days", type=int, default=0, help="ethereum: days before the window for the shadow burn-in")
    parser.add_argument("--train-days", type=int, default=0, help="ethereum: labelled days before the window to train both on")
    parser.add_argument("--out", type=Path, required=True, help="a directory: replay.json.gz and baseline.json go there")
    args = parser.parse_args()

    data = args.data.expanduser()
    if args.dataset == "ulb":
        (rows, t0, span, dated, burn), run_id, train = load_ulb(data), f"ulb-creditcard-{args.rounds}r", []
    else:
        if args.burn_in_days and args.train_days:
            parser.error("pick a burn-in or a training period, not both")
        rows, t0, span, dated, before = load_ethereum(data, args.days, args.max_payments, args.start,
                                                      args.burn_in_days or args.train_days)
        burn, train = (before, []) if args.burn_in_days else ([], before)
        run_id = f"eth-phishing-{datetime.fromtimestamp(t0, timezone.utc).date()}-{args.days}d-{args.rounds}r"
    run_id += ("-real" if args.real else "") + (f"-burnin{args.burn_in_days}d" if burn else "") + (
        f"-train{args.train_days}d" if train else "")
    replay, baseline = run(rows, t0, span, dated, args.rounds, run_id, DATASETS[args.dataset], args.real, burn, train)
    args.out.mkdir(parents=True, exist_ok=True)
    # Every payment is in the replay (the arena's scoreboard counts them all), so it's gzipped; the arena unzips it.
    (args.out / "replay.json.gz").write_bytes(gzip.compress(json.dumps(replay, separators=(",", ":"), ensure_ascii=False).encode(), 9, mtime=0))
    (args.out / "baseline.json").write_text(json.dumps(baseline, separators=(",", ":"), ensure_ascii=False))
    print(json.dumps(check(replay, baseline), indent=1))
    print(f"baseline {baseline['totals']}\nours {baseline['ours_totals']}\n{baseline['trained_on']}")


if __name__ == "__main__":
    main()

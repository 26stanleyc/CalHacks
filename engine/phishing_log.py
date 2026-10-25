"""The payment log behind the arena's /phishing/ page: one real phishing address's victims, in time order,
mixed with the honest payments just before each, and what TagTeam and XGBoost decided for every one.
Everything comes from the exported Ethereum run (replay.json.gz, baseline.json) and engine/eth_phishing.py's
arrays; nothing is re-decided here.

    uv run python -m engine.phishing_log --arrays ~/calhacks-sandbox/eth/arrays \\
        --run ../arena-real-data/arena/public/eth --train-from 2018-02-26

`reports`: how many phishing payments into the receiver were confirmed (labelled) before that payment's day,
from --train-from on: what TagTeam's receiver memory held when it decided. XGBoost's memory stopped at the
start of the test week (`reports_frozen`). The address followed (--address, or the one TagTeam stopped most
payments into) and the context per victim payment (--context) are the only choices made here.
"""

import argparse
import gzip
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DAY = 86_400


def main() -> None:
    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--arrays", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="the folder with replay.json.gz and baseline.json")
    parser.add_argument("--train-from", required=True, help="first training day (YYYY-MM-DD): where reports start counting")
    parser.add_argument("--address", type=int, help="node id of the phishing address to follow")
    parser.add_argument("--context", type=int, default=3, help="honest payments shown before each victim payment")
    args = parser.parse_args()

    a = args.arrays.expanduser()
    phishing = np.fromfile(a / "phishing.u8", dtype=np.uint8).astype(bool)
    src, dst = np.fromfile(a / "src.u32", dtype=np.uint32), np.fromfile(a / "dst.u32", dtype=np.uint32)
    amount, ts = np.fromfile(a / "amount.f64"), np.fromfile(a / "ts.f64")
    addresses = (a / "addresses.txt").read_text().split("\n")
    replay = json.loads(gzip.decompress((args.run / "replay.json.gz").read_bytes()))
    baseline = json.loads((args.run / "baseline.json").read_text())
    payments = [p for r in replay["rounds"] for w in r["waves"] for p in w["payments"]]  # time order
    index = {p["tx_id"]: i for i, p in enumerate(payments)}
    edge = lambda p: int(p["tx_id"][1:])

    target = args.address
    if target is None:  # the address TagTeam stopped the most payments into
        stopped = defaultdict(int)
        for p in payments:
            if p["is_fraud"] and not p["settled"]:
                stopped[int(dst[edge(p)])] += 1
        target = max(stopped, key=stopped.get)

    train_from = datetime.fromisoformat(args.train_from).replace(tzinfo=timezone.utc).timestamp()
    week_start = datetime.fromisoformat(replay["dataset"]["start"]).timestamp()
    confirmed = (phishing[src] == 0) & phishing[dst] & (np.round(amount * 100) > 0) & (ts >= train_from)
    report_ts = defaultdict(list)  # receiver -> times of its confirmed phishing payments
    for i in np.flatnonzero(confirmed):
        report_ts[int(dst[i])].append(float(ts[i]))

    def reports(receiver: int, before: float) -> int:
        return sum(t < before for t in report_ts.get(receiver, ()))

    victims = [i for i, p in enumerate(payments) if p["is_fraud"] and int(dst[edge(p)]) == target]
    shown: list[int] = []
    for i in victims:
        honest_before = [j for j in range(i - 1, max(-1, i - 400), -1) if not payments[j]["is_fraud"]][: args.context]
        shown += [j for j in reversed(honest_before) if j not in shown] + [i]

    def line(j: int) -> dict:
        p = payments[j]
        e = edge(p)
        day_start = float(ts[e]) // DAY * DAY
        theirs = baseline["decisions"].get(p["tx_id"], {"action": "allow", "settled": True})
        return {"tx_id": p["tx_id"], "ts": float(ts[e]), "from": addresses[src[e]], "to": addresses[dst[e]],
                "amount_cents": p["amount_cents"], "phishing": p["is_fraud"], "scam": int(dst[e]) == target,
                "tagteam": {"action": p["action"], "settled": p["settled"], "why": p["explanation"]},
                "xgboost": theirs, "reports": reports(int(dst[e]), day_start),
                "reports_frozen": reports(int(dst[e]), week_start)}

    log = [line(j) for j in shown]
    out = {"run_id": replay["run_id"], "dataset": replay["dataset"], "address": addresses[target],
           "victims": len(victims), "context": args.context, "lines": log}
    (args.run / "log.json").write_text(json.dumps(out, separators=(",", ":")))
    stopped_tt = sum(1 for l in log if l["scam"] and not l["tagteam"]["settled"])
    stopped_x = sum(1 for l in log if l["scam"] and not l["xgboost"]["settled"])
    print(f"{addresses[target]}: {len(victims)} victim payments, TagTeam stopped {stopped_tt}, XGBoost {stopped_x}; "
          f"{len(log)} lines -> {args.run / 'log.json'}")


if __name__ == "__main__":
    main()

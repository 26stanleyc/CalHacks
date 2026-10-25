"""TagTeam and XGBoost, each live against the same attackers (in the simulator).

engine/head_to_head.py replays TagTeam's recorded traffic through XGBoost, so no attacker ever adapts
to XGBoost. Here both defenses face the attackers live, round by round in lockstep, with the same
roster each round:
- outsider lineages and the hijacker: each run builds its own from the same seed;
- threat-intel attackers: XGBoost's run gets a fresh copy of each when TagTeam's run first fields it;
- spawned attackers: each starts from the playbook TagTeam's defenders spawned it with, then re-tunes
  between waves in a sandbox of the defense it is attacking (in XGBoost's run, XGBoost as it stands);
- archive replays: the attackers TagTeam's arena draws that round, with the playbook each ended up
  with in XGBoost's run.
From there every attacker adapts only to the defense in front of it. XGBoost alone guards its gate
(the same feature pipeline, XGBoost's decision), refitted on all labels so far after every round.
Both runs are scored the same way: costs under the cost matrix, and money through with every
step-up resolved by the engine's draw at the attacker's pass rate.

    uv run python -m engine.live_vs_xgboost --seed 42 --rounds 20 --out evidence/live/mock-42
"""

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

from engine.arena.sandbox import Sandbox
from engine.pipeline import DefensePipeline


class XGBoostPipeline(DefensePipeline):
    """XGBoost alone at the gate: the shared feature pipeline, XGBoost's decision."""

    def __init__(self, defense, **kwargs):
        super().__init__(**kwargs)
        self.defense = defense

    def score(self, tx, features):
        return self.defense.decide(tx, features)


class XGBoostSandbox(Sandbox):
    """Spawned attackers in XGBoost's run tune against XGBoost as it stands this round."""

    def __init__(self, defense, pipeline, world):
        super().__init__(pipeline, world.identities, world.products, world.round_seconds, mandate_for=world.fraud_mandate)
        self.defense = defense

    def _pipeline(self, identities):
        return XGBoostPipeline(self.defense, identities=identities, product_text=self.product_text)


def score_run(store, rounds: int) -> dict:
    """Per-round and total outcomes of one run, from its own final decisions."""
    from bench.metrics import cost_cents
    from contracts.schemas import Action
    from engine.engine import resolve
    from engine.export_baseline import step_up_pass
    from engine.orchestrator import CostMatrix

    costs = CostMatrix()
    sources = {r["attacker_id"]: r["source"] for r in store.rows["attackers"]}
    lineage = {r["attacker_id"]: (r["payload"] or {}).get("lineage") for r in store.rows["attackers"]}
    truth = {g.tx_id: g for g in store.ground_truth}
    final = {d.tx_id: d.action for d in store.decisions}
    per_round = []
    for rnd in range(1, rounds + 1):
        row = defaultdict(float)
        for t in (t for t in store.transactions if t.round == rnd and t.tx_id in final):
            g, act = truth[t.tx_id], final[t.tx_id]
            src = sources.get(t.attacker_id, "legit")
            src = getattr(src, "value", src)
            settled = resolve(t, g, SimpleNamespace(action=act), step_up_pass(src, lineage.get(t.attacker_id), costs),
                              costs.legit_step_up_abandon).settled
            if g.is_fraud:
                row["fraud_tried"] += t.amount_cents
                row["fraud_through"] += t.amount_cents if settled else 0
                row["cost"] += cost_cents(True, act, t.amount_cents)
            else:
                row["honest_through"] += t.amount_cents if settled else 0
                if not g.breaches_mandate:  # a blocked mandate breach isn't a false positive (CONTRACT_NOTES Q1)
                    row["cost"] += cost_cents(False, act, t.amount_cents)
                    row["honest"] += 1
                    row["flagged"] += act != Action.ALLOW
        per_round.append({k: (v / 100 if k not in ("honest", "flagged") else int(v)) for k, v in row.items()}
                         | {"net": (row["honest_through"] - row["fraud_through"]) / 100})
    total = {k: sum(r.get(k, 0) for r in per_round) for k in ("cost", "fraud_tried", "fraud_through", "honest_through", "net",
                                                                "honest", "flagged")}
    return {"total": total, "per_round": per_round}


def run(seed: int, rounds: int, real: bool) -> dict:
    from bench.baselines import trainable
    from engine.arena.arena import ArchiveRunner
    from engine.arena.spawning import InsiderAttacker
    from engine.engine import FraudEngine
    from engine.entrypoints import _brain, _voters
    from engine.head_to_head import XGBoostEveryRound
    from engine.intel.watchlist import IntelAttacker
    from engine.sim.marketplace import MarketplaceWorld
    from engine.store import MemoryStore

    brain = (lambda: {"brain": _brain()}) if real else (lambda: {})
    rosters: dict[int, list] = {}

    def capture(round_no, running):  # TagTeam's roster each round, before its waves change anyone
        rosters[round_no] = list(running)
        return running

    tagteam = FraudEngine(store=MemoryStore(), world=MarketplaceWorld(seed=seed), allowlist={"live"}, seed=seed,
                          run_id=f"live-tagteam-{seed}", voters=_voters(), threat_intel=True, roster=capture, **brain())

    world = MarketplaceWorld(seed=seed)
    xgb = XGBoostEveryRound()
    pipeline = XGBoostPipeline(xgb, identities=world.identities, product_text=world.product_text)
    sandbox = XGBoostSandbox(xgb, pipeline, world)
    mine: dict[str, object] = {}  # XGBoost's run's own adaptive attackers (outsiders, hijacker, threat intel)
    records: dict[str, object] = {}  # spawned attackers' latest record in XGBoost's run, for archive replays
    insiders: list = []

    def latest(aid, fallback):
        if aid in records:
            return records[aid]
        return mine[aid].record() if aid in mine else fallback

    def mirror(round_no, own):
        for attacker, _ in own:
            mine.setdefault(attacker.attacker_id, attacker)
        insiders.clear()
        out = []
        for attacker, n in rosters[round_no]:
            aid = attacker.attacker_id
            if isinstance(attacker, InsiderAttacker):
                # as spawned, before TagTeam's run re-tuned it this round
                twin = InsiderAttacker(attacker._record, sandbox, world.products, world.identities, world.round_seconds, seed,
                                       mandate_for=world.fraud_mandate)
                insiders.append(twin)
            elif isinstance(attacker, ArchiveRunner):
                twin = ArchiveRunner(latest(aid, attacker.record()), world.products, world.identities, world.round_seconds,
                                     world.fraud_mandate, tag=attacker.tag)
            elif isinstance(attacker, IntelAttacker):
                twin = mine.setdefault(aid, IntelAttacker(aid, attacker.persona, baseline.brain, world, attacker.introduced))
            else:
                twin = mine[aid]  # an outsider lineage or the hijacker, built by this run from the same seed
            out.append((twin, n))
        return out

    baseline = FraudEngine(store=MemoryStore(), world=world, pipeline=pipeline, allowlist={"live"}, seed=seed,
                           run_id=f"live-xgboost-{seed}", arena=False, threat_intel=False, escalation_cap=0, roster=mirror,
                           **brain())
    baseline.crs = None  # XGBoost alone: no CRS checks, no investigator (escalation_cap=0)

    started = time.monotonic()
    for rnd in range(1, rounds + 1):
        tagteam.run_round("live")
        baseline.run_round("live")
        for twin in insiders:
            records[twin.attacker_id] = twin.record()
        store = baseline.store
        features = {f.tx_id: f for f in store.features}
        truth = {g.tx_id: g for g in store.ground_truth}
        xgb.learn(rnd, [(t, features[t.tx_id], truth[t.tx_id]) for t in store.transactions
                        if t.round == rnd and t.tx_id in features and trainable(t)])
        print(f"round {rnd}/{rounds} done, {(time.monotonic() - started) / rnd:.0f}s per round", flush=True)

    same = [sorted(a.attacker_id for a, _ in rosters[r]) for r in range(1, rounds + 1)]
    # An attacker can run twice in a round (boosted threat intel and an archive draw), so compare counts.
    fielded = [sorted(r["attacker_id"] for r in baseline.store.rows["attackers"] if r["round"] == rnd)
               for rnd in range(1, rounds + 1)]
    mismatch = {rnd: {"tagteam": s, "xgboost": f} for rnd, (s, f) in enumerate(zip(same, fielded), 1) if s != f}
    return {"seed": seed, "rounds": rounds, "real": real, "seconds_per_round": (time.monotonic() - started) / rounds,
            "same_roster_every_round": not mismatch, "roster_mismatch": mismatch, "roster_sizes": [len(s) for s in same],
            "tagteam_live": score_run(tagteam.store, rounds), "xgboost_live": score_run(baseline.store, rounds)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--real", action="store_true", help="use the Bitdeer models (mock fallback per call)")
    parser.add_argument("--out", type=Path, required=True, help="output path without extension")
    args = parser.parse_args()
    out = run(args.seed, args.rounds, args.real)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(out, indent=1))
    t, x = out["tagteam_live"]["total"], out["xgboost_live"]["total"]
    print(f"same roster every round: {out['same_roster_every_round']}")
    for name, s in (("TagTeam, live", t), ("XGBoost every round, live", x)):
        print(f"{name:28} cost ${s['cost']:,.0f}  fraud tried ${s['fraud_tried']:,.0f}  through ${s['fraud_through']:,.0f}  "
              f"net ${s['net']:,.0f}  honest flagged {s['flagged']}/{s['honest']}")


if __name__ == "__main__":
    main()

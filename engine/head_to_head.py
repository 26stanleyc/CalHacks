"""Head-to-head on new attacks: the full system against standard trained baselines, on identical
traffic, in the simulator (a sandbox: memory store, simulated marketplace, no real money).

The full system runs N rounds. Every payment it saw (features included) is then replayed through:
- `xgboost_retrained`: XGBoost alone, retrained on all labels on the benchmark schedule (bench/);
- `xgboost_frozen`: XGBoost trained on the first FREEZE_AFTER rounds, then never updated, a model
  trained on yesterday's fraud;
- `static_rules`: the seed rulebooks, never updated;
- `rules_plus_xgboost`: the typical industry stack, static rules and a retrained XGBoost side by side,
  the stricter action wins.

New attack types arrive during the run: a threat-intel attacker family each round (from a report
the defense has never seen) and freshly spawned attackers. For each new attacker we compare what the
fraud cost in its first round and its first 3 rounds. A new attacker's first round isn't yet adapted
to any defense, so identical-traffic replay is fair there. Later rounds were adapted against the full
system, which favors the baselines, if anything.

    uv run python -m engine.head_to_head --seeds 42 43 44 --rounds 10 --out evidence/head-to-head/mock
    uv run python -m engine.head_to_head --real --seeds 42 --rounds 6 --out evidence/head-to-head/bitdeer

--record also writes the full system's run (every decision, rule change, reflection, critic check and
attacker) to the team database as it runs, so the dashboard can show it and a stopped run keeps every
finished round; its run id is printed.
"""

import argparse
import json
import statistics
import time
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

FREEZE_AFTER = 3
NEW_SOURCES = ("threat_intel", "spawned")
STRICTNESS = {"allow": 0, "step_up": 1, "block": 2}


class RulesPlusXGBoost:
    """Static rules and a retrained XGBoost side by side: the stricter action wins, the score is the
    higher of the two, and only XGBoost learns."""

    name = "rules_plus_xgboost"

    def __init__(self):
        from bench.baselines import StaticRulesDefense, XGBoostDefense

        self.rules, self.xgb = StaticRulesDefense(), XGBoostDefense()

    def decide(self, tx, features):
        a, b = self.rules.decide(tx, features), self.xgb.decide(tx, features)
        strict = max((a, b), key=lambda d: STRICTNESS[d.action.value])
        return strict.model_copy(update={"weighted_score": max(a.weighted_score, b.weighted_score)})

    def learn(self, round, labeled):
        self.xgb.learn(round, labeled)

    def freeze(self, on):
        self.xgb.freeze(on)


class XGBoostEveryRound:
    """XGBoost alone, refitted on all labels so far after every round (bench/'s schedule is every 2)."""

    name = "xgboost_every_round"

    def __init__(self):
        from bench.baselines import XGBoostDefense

        self.xgb = XGBoostDefense()

    def decide(self, tx, features):
        return self.xgb.decide(tx, features)

    def learn(self, round, labeled):
        from bench.baselines import trainable

        self.xgb.history.extend((fv, truth.is_fraud) for tx, fv, truth in labeled if trainable(tx))
        self.xgb.voter.retrain(self.xgb.history)

    def freeze(self, on):
        pass


def run_one(seed: int, rounds: int, real: bool, record: str = "") -> dict:
    from bench.baselines import StaticRulesDefense, XGBoostDefense, trainable
    from bench.metrics import cost_cents
    from contracts.schemas import Action
    from engine.engine import FraudEngine
    from engine.entrypoints import _brain, _voters
    from engine.sim.marketplace import MarketplaceWorld
    from engine.store import MemoryStore

    from engine.store import PostgresStore, TeeStore

    run_id = f"h2h-{'real' if real else 'mock'}-{seed}" + (f"-{record}" if record else "")
    database = PostgresStore() if record else None
    engine = FraudEngine(store=TeeStore(MemoryStore(), database) if record else MemoryStore(), world=MarketplaceWorld(seed=seed), allowlist={"h2h"}, seed=seed,
                         run_id=run_id, voters=_voters(), threat_intel=True,
                         **({"brain": _brain()} if real else {}))
    started = time.monotonic()
    for _ in range(rounds):
        engine.run_round(requested_by="h2h")
    seconds = time.monotonic() - started

    store = engine.store.primary if record else engine.store
    if database is not None:
        database.close()
    features = {f.tx_id: f for f in store.features}
    truth = {g.tx_id: g for g in store.ground_truth}
    full = {d.tx_id: d.action for d in store.decisions}  # the last decision per payment (after any escalation)
    sources = {r["attacker_id"]: r["source"] for r in store.rows["attackers"]}
    introduced: dict[str, int] = {}
    for r in store.rows["attackers"]:
        introduced.setdefault(r["attacker_id"], r["round"])
    contenders = {"xgboost_retrained": XGBoostDefense(), "xgboost_every_round": XGBoostEveryRound(),
                  "xgboost_frozen": XGBoostDefense(), "static_rules": StaticRulesDefense(), "rules_plus_xgboost": RulesPlusXGBoost()}

    # Money that actually got through, every contender's step-ups resolved by the engine's own draw at
    # each attacker's pass rate (engine.engine.resolve), so the full system is scored like the rest.
    from types import SimpleNamespace

    from engine.engine import resolve
    from engine.export_baseline import step_up_pass
    from engine.orchestrator import CostMatrix

    costs = CostMatrix()
    lineage = {r["attacker_id"]: (r["payload"] or {}).get("lineage") for r in store.rows["attackers"]}
    fraud_through: dict[str, float] = defaultdict(float)
    honest_through: dict[str, float] = defaultdict(float)

    # cost per (contender, attacker, round) for fraud; false positives per (contender, round)
    fraud_cost: dict[tuple, float] = defaultdict(float)
    honest: dict[tuple, list] = defaultdict(list)
    honest_cost: dict[str, float] = defaultdict(float)  # false blocks, friction and abandonment on honest shoppers
    for rnd in range(1, rounds + 1):
        txs = [t for t in store.transactions if t.round == rnd and t.tx_id in features and t.tx_id in full]
        actions = {"full": {t.tx_id: full[t.tx_id] for t in txs}}
        for name, defense in contenders.items():
            actions[name] = {t.tx_id: defense.decide(t, features[t.tx_id]).action for t in txs}
        for t in txs:
            g = truth[t.tx_id]
            pass_rate = step_up_pass(sources.get(t.attacker_id, "legit"), lineage.get(t.attacker_id), costs)
            for name, acts in actions.items():
                if resolve(t, g, SimpleNamespace(action=acts[t.tx_id]), pass_rate, costs.legit_step_up_abandon).settled:
                    if g.is_fraud:
                        fraud_through[name] += t.amount_cents
                    else:
                        honest_through[name] += t.amount_cents
                if g.is_fraud:
                    fraud_cost[(name, t.attacker_id, rnd)] += cost_cents(True, acts[t.tx_id], t.amount_cents)
                elif not g.breaches_mandate:
                    honest[(name, rnd)].append(acts[t.tx_id] != Action.ALLOW)
                    honest_cost[name] += cost_cents(False, acts[t.tx_id], t.amount_cents)
        labeled = [(t, features[t.tx_id], truth[t.tx_id]) for t in txs if trainable(t)]
        contenders["xgboost_frozen"].freeze(rnd >= FREEZE_AFTER)
        for defense in contenders.values():
            defense.learn(rnd, labeled)

    names = ["full", *contenders]
    new = [aid for aid, src in sources.items() if src in NEW_SOURCES and introduced[aid] > FREEZE_AFTER]
    spend = engine.brain.spend()
    failures: dict[str, int] = defaultdict(int)
    for f in engine.brain.failures:  # model calls that errored or didn't parse (the code fallback decided)
        failures[f"{f['role']}:{f['kind']}"] += 1
    out = {"seed": seed, "rounds": rounds, "real": real, "run_id": run_id if record else None,
           "seconds_per_round": seconds / rounds, "new_attackers": len(new),
           "llm_spend_usd": spend.get("cost_usd", 0.0), "llm_calls": spend.get("calls", 0),
           "llm_tokens": spend.get("input_tokens", 0) + spend.get("output_tokens", 0),
           "llm_failures": dict(failures), "by_contender": {}}
    for name in names:
        first = sum(fraud_cost[(name, aid, introduced[aid])] for aid in new)
        first3 = sum(fraud_cost[(name, aid, introduced[aid] + k)] for aid in new for k in range(3))
        total = sum(v for (n, _, _), v in fraud_cost.items() if n == name)
        fps = [statistics.mean(honest[(name, rnd)]) for rnd in range(1, rounds + 1) if honest[(name, rnd)]]
        out["by_contender"][name] = {"new_first_round_usd": first / 100, "new_first_3_rounds_usd": first3 / 100,
                                     "all_fraud_usd": total / 100, "honest_cost_usd": honest_cost[name] / 100,
                                     "total_cost_usd": (total + honest_cost[name]) / 100, "fp_mean": statistics.mean(fps), "fp_max": max(fps),
                                     "fraud_through_usd": fraud_through[name] / 100, "honest_through_usd": honest_through[name] / 100,
                                     "net_usd": (honest_through[name] - fraud_through[name]) / 100}
    last = {d.tx_id: d for d in store.decisions}  # the final decision per payment
    checked = [d.crs for d in last.values() if d.crs is not None]
    out["crs"] = {"payments_with_crs": len(checked),
                  "by_action": {a: sum(c.decision.value == a for c in checked) for a in ("allow", "step_up", "block")},
                  "watch_added": sum(c.watch == "added" for c in checked), "on_watch_list": sum(c.watch == "on_list" for c in checked),
                  "fraud_checked": sum(c.tx_id in truth and truth[c.tx_id].is_fraud for c in checked)}
    return out


def markdown(runs: list[dict]) -> str:
    names = list(runs[0]["by_contender"])

    def agg(name, key, fmt):
        values = [r["by_contender"][name][key] for r in runs]
        sd = statistics.stdev(values) if len(values) > 1 else 0.0
        return f"{fmt.format(statistics.mean(values))} ± {fmt.format(sd)}"

    seeds = ", ".join(str(r["seed"]) for r in runs)
    lines = [f"Seeds {seeds}; {runs[0]['rounds']} rounds; {'Bitdeer models' if runs[0]['real'] else 'mock models'}; "
             f"new attackers after round {FREEZE_AFTER}: {sum(r['new_attackers'] for r in runs)}; "
             f"LLM calls {sum(r.get('llm_calls', 0) for r in runs):,}, tokens {sum(r.get('llm_tokens', 0) for r in runs):,}, "
             f"failed calls {sum(sum(r.get('llm_failures', {}).values()) for r in runs):,}; "
             f"{statistics.mean(r.get('seconds_per_round', 0) for r in runs) / 60:.1f} min per round. "
             f"Mean ± standard deviation across seeds.", "",
             "| contender | new attacks, first round | new attacks, first 3 rounds | all fraud | honest shoppers | total cost | mean FP | max FP |",
             "|---|---|---|---|---|---|---|---|"]
    for name in names:
        lines.append(f"| {name} | {agg(name, 'new_first_round_usd', '${:,.0f}')} | "
                     f"{agg(name, 'new_first_3_rounds_usd', '${:,.0f}')} | {agg(name, 'all_fraud_usd', '${:,.0f}')} | "
                     f"{agg(name, 'honest_cost_usd', '${:,.0f}')} | {agg(name, 'total_cost_usd', '${:,.0f}')} | "
                     f"{agg(name, 'fp_mean', '{:.2%}')} | {max(r['by_contender'][name]['fp_max'] for r in runs):.2%} |")
    return "\n".join(lines) + ("\n\nCosts under the cost matrix: fraud columns are missed fraud plus friction on caught "
                               "fraud; \"honest shoppers\" is false blocks, step-up friction and abandoned purchases; "
                               "total is both.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--real", action="store_true", help="use the Bitdeer models (mock fallback per call)")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--record", action="store_true", help="write the full system's runs to the team database")
    parser.add_argument("--out", type=Path, required=True, help="output path without extension")
    args = parser.parse_args()

    with ProcessPoolExecutor(args.workers) as pool:
        stamp = datetime.now().strftime("%Y%m%d%H%M") if args.record else ""
        runs = list(pool.map(run_one, args.seeds, [args.rounds] * len(args.seeds), [args.real] * len(args.seeds),
                             [stamp] * len(args.seeds)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(runs, indent=1))
    table = markdown(runs)
    recorded = [r["run_id"] for r in runs if r.get("run_id")]
    if recorded:
        table += f"\nRecorded in the team database (pick these in the dashboard): {', '.join(recorded)}\n"
    args.out.with_suffix(".md").write_text(table)
    print(table)


if __name__ == "__main__":
    main()

"""Multi-seed experiment runner for engine development: runs engine setups over several seeds, in
parallel, and writes a per-round table (JSON) and a summary (markdown) for before/after comparisons.

This is the engine's own A/B tool, on whatever brain is configured (mock unless Bitdeer is set
up). Ryan's precompute runner (R7) owns the official benchmark and its baselines.

    uv run python -m engine.experiments --seeds 42 43 44 --rounds 10 --out evidence/experiments/baseline
"""

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# Each setup is FraudEngine keyword arguments on top of the full system (as build_engine runs it).
SETUPS = {
    "full": {},
    "outsiders_only": {"arena": False},
    "no_xgb_voter": {"voters": []},
    "intel_off": {"intel_watch": False},
}
FP_CAP = 0.02


def run_one(setup: str, seed: int, rounds: int, tuning_tries: int | None) -> dict:
    from bench.xgb_voter import XGBoostVoter
    from engine.engine import FraudEngine
    from engine.sim.marketplace import MarketplaceWorld

    kwargs = {"voters": [XGBoostVoter()], "threat_intel": True, **SETUPS[setup]}
    engine = FraudEngine(world=MarketplaceWorld(seed=seed), allowlist={"experiments"}, seed=seed,
                         run_id=f"exp-{setup}-{seed}", tuning_tries=tuning_tries, **kwargs)
    rows = []
    for _ in range(rounds):
        m = engine.run_round(requested_by="experiments")
        mine = [r for r in engine.store.rows["attackers"] if r["round"] == m.round]
        outsider = sum(r["payload"]["extraction_cents"] for r in mine if r["source"] == "outsider")
        hijacker = [r["payload"] for r in mine if r["attacker_id"] == "outsider-hijacker"]
        rows.append({"round": m.round, "fp": m.false_positive_rate, "total_cost_cents": m.total_cost_cents,
                     "outsider_loss_cents": outsider, "escalation_rate": m.escalation_rate,
                     "hijacker_caught": hijacker[0]["caught"] if hijacker else 0,
                     "hijacker_attempts": hijacker[0]["attempts"] if hijacker else 0, "exploitability_cents": m.exploitability_cents,
                     "llm_spend_usd": m.llm_spend_usd})
    return {"setup": setup, "seed": seed, "rounds": rows}


def summarize(runs: list[dict]) -> list[dict]:
    out = []
    for setup in dict.fromkeys(r["setup"] for r in runs):
        mine = [r["rounds"] for r in runs if r["setup"] == setup]

        def stat(values):
            return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)

        out.append({
            "setup": setup, "seeds": len(mine),
            "outsider_last_vs_first": stat([rs[-1]["outsider_loss_cents"] / max(1, rs[0]["outsider_loss_cents"]) for rs in mine]),
            "total_cost_usd": stat([sum(x["total_cost_cents"] for x in rs) / 100 for rs in mine]),
            "fp_mean": stat([statistics.mean(x["fp"] for x in rs) for rs in mine]),
            "fp_max": max(x["fp"] for rs in mine for x in rs),
            "rounds_over_cap": sum(x["fp"] > FP_CAP for rs in mine for x in rs),
            "hijacker_caught": stat([sum(x["hijacker_caught"] for x in rs) / max(1, sum(x["hijacker_attempts"] for x in rs))
                                     for rs in mine]),
            "llm_spend_usd": stat([sum(x["llm_spend_usd"] for x in rs) for rs in mine]),
        })
    return out


def markdown(summary: list[dict], seeds: list[int], rounds: int) -> str:
    def pm(pair, fmt):
        return f"{fmt.format(pair[0])} ± {fmt.format(pair[1])}"

    lines = [f"Seeds {', '.join(map(str, seeds))}; {rounds} rounds each. Mean ± standard deviation across seeds.", "",
             "| setup | outsider loss, last round / first | total cost | mean FP | max FP | rounds over 2% FP | hijacker caught | LLM spend |",
             "|---|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(f"| {s['setup']} | {pm(s['outsider_last_vs_first'], '{:.0%}')} | {pm(s['total_cost_usd'], '${:,.0f}')} | "
                     f"{pm(s['fp_mean'], '{:.2%}')} | {s['fp_max']:.2%} | {s['rounds_over_cap']} | "
                     f"{pm(s['hijacker_caught'], '{:.0%}')} | {pm(s['llm_spend_usd'], '${:.2f}')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--setups", nargs="+", default=list(SETUPS), choices=list(SETUPS))
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 43, 44])
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--tuning-tries", type=int, default=None, help="sandbox tuning budget (lower is faster)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True, help="output path without extension")
    args = parser.parse_args()

    jobs = [(setup, seed) for setup in args.setups for seed in args.seeds]
    with ProcessPoolExecutor(args.workers) as pool:
        runs = list(pool.map(run_one, *zip(*jobs), [args.rounds] * len(jobs), [args.tuning_tries] * len(jobs)))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(json.dumps(runs, indent=1))
    table = markdown(summarize(runs), args.seeds, args.rounds)
    args.out.with_suffix(".md").write_text(table)
    print(table)


if __name__ == "__main__":
    main()

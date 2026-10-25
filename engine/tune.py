"""Tune TagTeam's own parameters against live XGBoost (engine/live_vs_xgboost.py), in parallel.

Each trial is one config on one seed, run in a fresh process so overrides never leak between trials.
Only TagTeam's decision and learning knobs are touched (KNOBS); the world, the attackers, the labels,
the XGBoost baseline and the scoring stay as they are.

    uv run python -m engine.tune --configs configs.json --seeds 1 2 --rounds 10 --workers 8 --out trials.jsonl

configs.json maps a config name to {knob: value}; {} is the default TagTeam.
"""

import argparse
import importlib
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

FP_CAP = 0.02  # the spec's per-round cap on honest shoppers flagged


def _set(module: str, name: str):
    """A knob that patches a module global (read at call time by the code that uses it)."""
    def apply(value, engine=None):
        if engine is None:
            setattr(importlib.import_module(module), name, value)
    return apply


def _decision_cost(field: str):
    """One of TagTeam's own cost assumptions, for its decisions only (the scorer keeps the real matrix)."""
    def apply(value, engine=None):
        if engine is None:
            import dataclasses

            from engine import orchestrator

            base = orchestrator.DECISION_COSTS or orchestrator.CostMatrix()
            orchestrator.DECISION_COSTS = dataclasses.replace(base, **{field: value})
    return apply


def _specialists_off(value, engine=None):
    """Ablation: every specialist lane votes allow with score 0 (to measure what the lanes add)."""
    if engine is None and value:
        from contracts.schemas import Action
        from engine import specialists

        real = specialists.Specialist.vote
        specialists.Specialist.vote = lambda self, tx, features: real(self, tx, features).model_copy(
            update={"score": 0.0, "vote": Action.ALLOW, "rule_ids": []})


def _investigator_timeout(value, engine=None):
    """Seconds TagTeam's investigator may spend on a case (set on the engine's own investigator)."""
    if engine is not None:
        engine.investigator.timeout_s = value


def _passthrough(value, engine=None):
    """The graph specialist's pass-through signal (engine/passthrough.py), TagTeam only."""
    if engine is not None and value:
        from engine import passthrough

        passthrough.apply(engine)


# name -> apply(value, engine): called once with engine=None before the engines are built (module
# globals), and once with the TagTeam engine right after it's built (instance attributes). All of
# them change TagTeam's own decisions or learning only (the parameter map: no world, attacker, label,
# baseline or scoring setting is here).
KNOBS: dict = {
    "split": _set("engine.orchestrator", "SPLIT_BLOCK"),
    "split_block_at": _set("engine.orchestrator", "SPLIT_BLOCK_AT"),
    "escalation_share": _set("engine.engine", "ESCALATION_SHARE"),  # 0: no investigator
    "investigator_timeout": _investigator_timeout,
    "passthrough": _passthrough,
    "voter_floor_until": _set("engine.orchestrator", "VOTER_FLOOR_UNTIL"),  # 1: floor at own XGBoost voter in round 2
    "combiner_drop_voters": _set("engine.learning.loop", "STACK_DROP_VOTERS"),  # e.g. ["anomaly"]
    "bootstrap_voters": _set("engine.learning.loop", "STACK_BOOTSTRAP_VOTERS"),  # e.g. ["xgboost"]
    "guard_tighten_factor": _set("engine.learning.loop", "STACK_GUARD_TIGHTEN_FACTOR"),
    "guard_relax_factor": _set("engine.learning.loop", "STACK_GUARD_RELAX_FACTOR"),
    "warmup_rounds": _set("engine.learning.loop", "STACK_WARMUP_ROUNDS"),
    "step_up_pass_belief": _decision_cost("fraud_step_up_pass"),
    "false_block_margin_belief": _decision_cost("false_block_margin"),  # 1.0: a wrongly blocked sale is lost in full
    "stack_at": _set("engine.learning.loop", "STACK_AT"),
    "calibration_folds": _set("engine.learning.loop", "STACK_CALIBRATION_FOLDS"),
    "calibration_rounds": _set("engine.learning.loop", "STACK_CALIBRATION_ROUNDS"),
    "bootstrap_at": _set("engine.learning.loop", "STACK_BOOTSTRAP_AT"),
    "guard_tighten_above": _set("engine.learning.loop", "STACK_GUARD_TIGHTEN_ABOVE"),
    "guard_relax_below": _set("engine.learning.loop", "STACK_GUARD_RELAX_BELOW"),
    "guard_min": _set("engine.learning.loop", "STACK_GUARD_MIN"),
    "window": _set("engine.learning.stacking", "WINDOW_ROUNDS"),  # lowering only (the deques are sized at 6)
    "retire_after": _set("engine.learning.loop", "RETIRE_AFTER"),
    # the specialists: how readily they vote, and how readily their learned rules are kept
    "specialists_off": _specialists_off,
    "vote_block_at": _set("engine.specialists", "VOTE_BLOCK_AT"),
    "vote_step_up_at": _set("engine.specialists", "VOTE_STEP_UP_AT"),
    "rule_honest_share": _set("engine.learning.reflection", "HONEST_SHARE_FOR_RULE"),
    "pair_honest_share": _set("engine.learning.reflection", "HONEST_SHARE_FOR_PAIR"),
    "holdout_honest_share": _set("engine.learning.loop", "HOLDOUT_HONEST_SHARE"),
    "revet_honest_share": _set("engine.learning.loop", "REVET_HONEST_SHARE"),
    "min_honest_rounds": _set("engine.learning.loop", "MIN_HONEST_ROUNDS"),
    "min_honest_samples": _set("engine.learning.loop", "MIN_HONEST_SAMPLES"),
    "guard_at": _set("engine.learning.loop", "GUARD_AT"),
    "vet_headroom": _set("engine.learning.critic", "VET_HEADROOM"),
}


# The tuned TagTeam in the demo (PaySim, held-out seeds 42-44: cost 16.1% and money lost 16.2% below XGBoost
# retrained every round, on average; see evidence in the build log). The investigator is off: on mock models it
# blocked honest payments.
TUNED = {"split": True, "stack_at": 0.9, "split_block_at": 0.90, "escalation_share": 0.0}


def trial(name: str, config: dict, seed: int, rounds: int, world: str = "paysim", real: bool = False) -> dict:
    from engine.live_vs_xgboost import run

    unknown = set(config) - set(KNOBS)
    if unknown:
        raise KeyError(f"unknown knobs: {sorted(unknown)}")
    for knob, value in config.items():
        KNOBS[knob](value)
    started = time.monotonic()
    if real:
        import integrations.settings  # noqa: F401  (loads .env: LLM_PROVIDER, API keys)
    out = run(seed, rounds, real=real, configure=lambda engine: [KNOBS[k](v, engine) for k, v in config.items()], world=world)
    t, x = out["tagteam_live"], out["xgboost_live"]
    fp = [r.get("flagged", 0) / max(1, r.get("honest", 0)) for r in t["per_round"]]
    return {"name": name, "config": config, "seed": seed, "rounds": rounds, "seconds": round(time.monotonic() - started),
            "same_roster": out["same_roster_every_round"], "tagteam": t["total"], "xgboost": x["total"],
            "gap_cost": t["total"]["cost"] - x["total"]["cost"],
            # from round 2 on, once XGBoost has labels (the headline: round 1 is XGBoost's cold start)
            "r2_cost_better": 1 - sum(r["cost"] for r in t["per_round"][1:]) / max(1, sum(r["cost"] for r in x["per_round"][1:])),
            "r2_money_better": 1 - sum(r["fraud_through"] + r["honest_tried"] - r["honest_through"] for r in t["per_round"][1:])
                               / max(1, sum(r["fraud_through"] + r["honest_tried"] - r["honest_through"] for r in x["per_round"][1:])),
            # money lost: fraud that got through plus honest money that didn't (same honest traffic in both runs)
            "money_lost_vs_xgb": (t["total"]["fraud_through"] - x["total"]["fraud_through"])
                                 + (x["total"]["honest_through"] - t["total"]["honest_through"]), "max_fp": max(fp), "fp_by_round": [round(f, 4) for f in fp],
            "within_cap": max(fp) <= FP_CAP, "per_round_cost": [round(r["cost"]) for r in t["per_round"]],
            "xgb_per_round_cost": [round(r["cost"]) for r in x["per_round"]], "investigator": out.get("investigator")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--world", choices=("marketplace", "paysim"), default="paysim")
    parser.add_argument("--real", action="store_true", help="real AI brains (LLM_PROVIDER=claude: Claude stand-ins; else Bitdeer)")
    parser.add_argument("--out", type=Path, required=True, help="JSONL, one line per finished trial (appended)")
    args = parser.parse_args()
    configs = json.loads(args.configs.read_text())
    jobs = [(name, cfg, seed) for name, cfg in configs.items() for seed in args.seeds]
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(args.workers, mp_context=ctx, max_tasks_per_child=1) as pool:
        futures = {pool.submit(trial, name, cfg, seed, args.rounds, args.world, args.real): (name, seed) for name, cfg, seed in jobs}
        for future in as_completed(futures):
            name, seed = futures[future]
            try:
                row = future.result()
            except Exception as e:  # one broken config shouldn't stop the sweep
                row = {"name": name, "seed": seed, "rounds": args.rounds, "error": repr(e)}
            with args.out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"{name} seed {seed}: " + (row.get("error") or
                  f"TagTeam ${row['tagteam']['cost']:,.0f} vs XGBoost ${row['xgboost']['cost']:,.0f}, max FP {row['max_fp']:.2%}"),
                  flush=True)


if __name__ == "__main__":
    main()

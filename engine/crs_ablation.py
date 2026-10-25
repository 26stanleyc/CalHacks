"""What CRS is worth: the same traffic decided with the bureau and without it.

The judging question for the CRS track is how central CRS data is to the product rather than how
many endpoints it touches, and the only honest way to answer that is to turn it off and measure.
Three arms, identical traffic and seeds:

- `off`: no CRS at all. The identity specialist votes on the simulator's own identity profile, which
  is where this project started.
- `escalation`: CRS only on payments whose score already landed in the uncertain band. This is what
  CRS was before enrollment: a few payments a round.
- `enrolled`: CRS resolves every person when their agent first appears, so the identity and graph
  specialists vote with the bureau's answer on every payment and the trust line underwrites each
  mandate. The escalation path still runs on top.

    uv run python -m engine.crs_ablation --seeds 42 43 44 --rounds 4 --out evidence/crs/ablation.md
"""

import argparse
import statistics
from dataclasses import dataclass

from engine.crs import CrsEscalation, MockCrs
from engine.engine import FraudEngine
from engine.sim.marketplace import MarketplaceWorld

TEAM = "agent1qteam"
ARMS = ("off", "escalation", "enrolled")


@dataclass
class Result:
    arm: str
    seed: int
    fraud_cents: int  # fraud that was allowed and settled
    fp_rate: float  # honest payments wrongly flagged
    caught: float  # share of fraud stopped
    over_cap_vetoes: int
    crs_rules_fired: int


def run_one(arm: str, seed: int, rounds: int) -> Result:
    world = MarketplaceWorld(seed=seed)
    crs = None if arm == "off" else CrsEscalation(MockCrs(world.identities, set(world.identities)))
    engine = FraudEngine(world=world, allowlist={TEAM}, seed=seed, arena=False, hijacker=True,
                         crs=crs, crs_enrollment=(arm == "enrolled"))
    for _ in range(rounds):
        engine.run_round(requested_by=TEAM)

    truth = {g.tx_id: g for g in engine.store.ground_truth}
    fraud_allowed = honest = flagged = fraud_total = fraud_stopped = 0
    for d in engine.store.decisions:
        g = truth.get(d.tx_id)
        if g is None:
            continue
        if g.is_fraud:
            fraud_total += 1
            if d.action.value == "allow":
                fraud_allowed += next((t.amount_cents for t in engine.store.transactions
                                       if t.tx_id == d.tx_id), 0)
            else:
                fraud_stopped += 1
        else:
            honest += 1
            flagged += d.action.value != "allow"
    fired = sum(1 for d in engine.store.decisions for v in d.votes
                for r in (v.rule_ids or []) if "crs" in r)
    return Result(arm, seed, fraud_allowed, flagged / max(honest, 1), fraud_stopped / max(fraud_total, 1),
                  sum(1 for d in engine.store.decisions if d.vetoed_by and "cap" in d.vetoed_by), fired)


def report(results: list[Result], rounds: int) -> str:
    out = ["# What CRS is worth", "",
           f"Identical traffic and seeds, {rounds} rounds each, mock bureau (so this is repeatable "
           "offline; the live sandbox answers the same products).", "",
           "| CRS | fraud settled | fraud stopped | false positives | over-cap vetoes | CRS rules fired |",
           "|---|---|---|---|---|---|"]
    labels = {"off": "off (where we started)", "escalation": "escalation only (uncertain band)",
              "enrolled": "enrolled (every person, every payment)"}
    for arm in ARMS:
        rows = [r for r in results if r.arm == arm]
        if not rows:
            continue
        out.append(f"| {labels[arm]} | ${statistics.mean(r.fraud_cents for r in rows) / 100:,.0f} | "
                   f"{statistics.mean(r.caught for r in rows):.1%} | "
                   f"{statistics.mean(r.fp_rate for r in rows):.2%} | "
                   f"{statistics.mean(r.over_cap_vetoes for r in rows):.0f} | "
                   f"{statistics.mean(r.crs_rules_fired for r in rows):.0f} |")
    out += ["", f"Mean across {len({r.seed for r in results})} seeds. 'Over-cap vetoes' are payments "
            "refused because they exceeded the mandate's cap: with enrollment on, that cap is the "
            "one CRS underwrote, so an identity the bureau cannot stand behind is held to a floor "
            "before any scoring happens.", "",
            "Reproduce: `uv run python -m engine.crs_ablation --seeds "
            + " ".join(str(s) for s in sorted({r.seed for r in results})) + f" --rounds {rounds}`"]
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    results = []
    for seed in args.seeds:
        for arm in ARMS:
            results.append(run_one(arm, seed, args.rounds))
            print(f"  {arm:11} seed {seed}: {results[-1]}", flush=True)
    text = report(results, args.rounds)
    print("\n" + text)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

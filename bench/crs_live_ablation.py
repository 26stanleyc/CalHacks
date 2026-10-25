"""Eric's CRS ablation (`engine/crs_ablation.py`) against the real CRS sandbox instead of the mock bureau.

Same three arms, traffic, seeds and scoring; the only change is the bureau behind `CrsEscalation`:
`HttpCrs` with CRS_BASE_URL, CRS_USERNAME and CRS_PASSWORD from the repo-root .env. The sandbox only
answers for CRS's 20 test persons, and `HttpCrs` sends each simulated person as one of them, so every
answer is a canned sandbox response (no credit pulls, no live vendor). Brains are the engine's mock
brains: no LLM calls.

    uv run python -m bench.crs_live_ablation --seeds 42 43 44 --rounds 4 --out evidence/crs/ablation-live.md

`--bureau mock` runs the unchanged mock-bureau ablation, for a check without credentials.
"""

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from engine import crs_ablation
from engine.crs import HttpCrs

MOCK_NOTE = "mock bureau (so this is repeatable offline; the live sandbox answers the same products)"
LIVE_NOTE = "live CRS sandbox (each simulated person sent as one of CRS's 20 test persons)"
REQUIRED = ("CRS_BASE_URL", "CRS_USERNAME", "CRS_PASSWORD")


def use_live_bureau() -> list[HttpCrs]:
    """Swap the ablation's bureau for the real sandbox; returns the clients made, to count their calls."""
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"the live bureau needs {', '.join(missing)} in .env")
    clients: list[HttpCrs] = []

    def live(identities, on_file):
        client = HttpCrs(os.environ["CRS_BASE_URL"], os.environ["CRS_USERNAME"], os.environ["CRS_PASSWORD"],
                         identities, on_file)
        clients.append(client)
        return client

    crs_ablation.MockCrs = live
    return clients


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--bureau", choices=("live", "mock"), default="live")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    clients = use_live_bureau() if args.bureau == "live" else []
    results = []
    for seed in args.seeds:
        for arm in crs_ablation.ARMS:
            results.append(crs_ablation.run_one(arm, seed, args.rounds))
            print(f"  {arm:11} seed {seed}: {results[-1]}", flush=True)
    text = crs_ablation.report(results, args.rounds)
    if args.bureau == "live":
        calls = sum(len(c.requests) for c in clients)
        text = text.replace(MOCK_NOTE, LIVE_NOTE).replace(
            "Reproduce: `uv run python -m engine.crs_ablation", "Reproduce: `uv run python -m bench.crs_live_ablation")
        text += f"\n{calls} live sandbox requests across {len(clients)} bureau clients.\n"
    print("\n" + text)
    if args.out:
        Path(args.out).write_text(text)
        print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

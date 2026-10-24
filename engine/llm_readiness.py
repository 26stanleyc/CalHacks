"""Real-model readiness check: runs mock rounds while recording every LLM call the engine makes,
then reports, per role, how many calls a round makes, how big the prompts are, which model answers
and what a round would cost at Bitdeer prices (integrations/model_prices.py; unknown prices are
reported, not guessed). It also saves one real prompt per role as a fixture, so tests can check the
prompts real models will see.

    uv run python -m engine.llm_readiness --rounds 6 --fixtures engine/tests/fixtures/prompts
    uv run python -m engine.llm_readiness --replay engine/tests/fixtures/prompts   # once Bitdeer is live

--replay sends each saved prompt to the real model for its role (BitdeerLLM, no mock fallback) and
reports whether the answer parses into its schema, with real token counts and latency.

Token counts are estimates (about 3.5 characters a token for JSON-heavy text); output tokens are
estimated from the mock's answers, which are shorter than a real model's, so treat the cost as a floor.
"""

import argparse
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from engine.brains import _MOCKS, MockBrains

CHARS_PER_TOKEN = 3.5
CONTEXT_BUDGET_TOKENS = 24_000  # comfortably inside every assigned model's context window


def tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


class RecordingBrains(MockBrains):
    """The mock brains, recording each call: role, schema, prompt and answer sizes."""

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []

    def complete(self, messages, *, role, response_schema=None, tools=None, max_tokens=1024):
        response = super().complete(messages, role=role, response_schema=response_schema, tools=tools,
                                    max_tokens=max_tokens)
        answer = response.parsed.model_dump_json() if response.parsed is not None else (response.text or "")
        self.records.append({"role": role, "schema": response_schema.__name__ if response_schema else None,
                             "messages": [{"role": m.role, "content": m.content} for m in messages],
                             "input_tokens": sum(tokens(m.content) for m in messages), "output_tokens": tokens(answer),
                             "max_tokens": max_tokens})
        return response


def record(rounds: int, seed: int) -> tuple[list[dict], int]:
    from bench.xgb_voter import XGBoostVoter
    from engine.brains import Brain
    from engine.engine import FraudEngine
    from engine.sim.marketplace import MarketplaceWorld

    llm = RecordingBrains()
    engine = FraudEngine(world=MarketplaceWorld(seed=seed), allowlist={"readiness"}, seed=seed, brain=Brain(llm),
                         voters=[XGBoostVoter()], threat_intel=True)
    for _ in range(rounds):
        engine.run_round(requested_by="readiness")
    return llm.records, rounds


def report(records: list[dict], rounds: int) -> tuple[list[dict], float, list[str]]:
    from contracts.config import model_for
    from integrations.model_prices import cost_usd, price_for

    by_role: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_role[r["role"]].append(r)
    rows, total, unpriced = [], 0.0, set()
    for role, calls in sorted(by_role.items()):
        model = model_for(role)
        ins = [c["input_tokens"] for c in calls]
        outs = [c["output_tokens"] for c in calls]
        cost = sum(cost_usd(model, i, o) for i, o in zip(ins, outs)) / rounds
        if price_for(model) == (None, None):
            unpriced.add(model)
        total += cost
        rows.append({"role": role, "model": model, "calls_per_round": len(calls) / rounds,
                     "input_mean": statistics.mean(ins), "input_max": max(ins), "output_mean": statistics.mean(outs),
                     "tokens_per_round": (sum(ins) + sum(outs)) / rounds, "usd_per_round": cost})
    return rows, total, sorted(unpriced)


def schemas() -> dict:
    """Every answer schema the engine asks for, by name (each has a registered mock answer)."""
    import engine.engine  # noqa: F401  (imports every module that registers a mock answer)
    import engine.intel.watchlist  # noqa: F401
    return {schema.__name__: schema for schema in _MOCKS}


def replay(folder: Path) -> int:
    from contracts.llm import Message
    from integrations.bitdeer import BitdeerLLM

    llm, by_name, failed = BitdeerLLM(), schemas(), 0
    print("| fixture | parsed | input / output tokens | seconds | problem |")
    print("|---|---|---|---|---|")
    for path in sorted(folder.glob("*.json")):
        r = json.loads(path.read_text())
        schema = by_name[r["schema"]]
        start = time.monotonic()
        try:
            response = llm.complete([Message(**m) for m in r["messages"]], role=r["role"], response_schema=schema,
                                    max_tokens=r["max_tokens"])
            parsed = response.parsed if isinstance(response.parsed, schema) else schema.model_validate_json(response.text)
            row = (True, f"{response.input_tokens} / {response.output_tokens}", "")
        except Exception as exc:  # report every failure and keep going
            parsed, row = None, (False, "-", str(exc)[:120].replace("|", "/"))
        failed += parsed is None
        print(f"| {path.stem} | {'yes' if row[0] else 'NO'} | {row[1]} | {time.monotonic() - start:.1f} | {row[2]} |")
    print(f"\n{failed} of {len(list(folder.glob('*.json')))} prompts failed to parse.")
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rounds", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fixtures", type=Path, help="save one real prompt per role here")
    parser.add_argument("--replay", type=Path, help="send saved prompts to the real models instead")
    args = parser.parse_args()
    if args.replay:
        raise SystemExit(1 if replay(args.replay) else 0)

    records, rounds = record(args.rounds, args.seed)
    rows, total, unpriced = report(records, rounds)
    print(f"{len(records)} LLM calls over {rounds} mock rounds\n")
    print("| role | model | calls/round | input tokens mean / max | output tokens mean | tokens/round | $/round |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['role']} | {r['model']} | {r['calls_per_round']:.1f} | {r['input_mean']:,.0f} / {r['input_max']:,} | "
              f"{r['output_mean']:,.0f} | {r['tokens_per_round']:,.0f} | ${r['usd_per_round']:.3f} |")
    print(f"\nTotal: {sum(r['tokens_per_round'] for r in rows):,.0f} tokens and ${total:.2f} per round "
          f"(budget $0.60; the run pauses above $1.20).")
    if unpriced:
        print(f"No price recorded for {', '.join(unpriced)}: counted as $0. Set BITDEER_PRICE_IN_/OUT_<MODEL>.")
    over = [r["role"] for r in rows if r["input_max"] > CONTEXT_BUDGET_TOKENS]
    if over:
        print(f"Prompts over {CONTEXT_BUDGET_TOKENS:,} tokens: {', '.join(over)}")
    if args.fixtures:
        args.fixtures.mkdir(parents=True, exist_ok=True)
        largest: dict[str, dict] = {}
        for r in records:  # the largest prompt per role and schema: the worst case a real model sees
            key = f"{r['role']}__{r['schema']}"
            if key not in largest or r["input_tokens"] > largest[key]["input_tokens"]:
                largest[key] = r
        for key, r in largest.items():
            (args.fixtures / f"{key}.json").write_text(json.dumps(r, indent=1))
        print(f"Saved {len(largest)} prompt fixtures to {args.fixtures}")


if __name__ == "__main__":
    main()

"""Demo view: one payment hopping between the engine's uAgents, message by message.

    uv run python -m engine.trace                     # a card tester's 8th tiny payment: blocked
    uv run python -m engine.trace --scenario legit    # an honest shopper's payment: allowed
    uv run python -m engine.trace --pace 0            # print everything at once

The payment really travels through the Bureau as uAgents messages; nothing is written to the
database. Each hop is timed as it arrives, then printed at `--pace` seconds per line so an
audience can follow it.
"""

import argparse
import os
import socket
import sys
import time

from contracts.schemas import Decision, FeatureVector, Transaction, Vote
from engine.bureau import EngineBureau, Hop
from engine.pipeline import DefensePipeline
from engine.sim.world import World

ACTION_COLORS = {"allow": "32", "step_up": "33", "block": "31"}


def scenario(name: str) -> tuple[World, list[Transaction], Transaction, str]:
    """(world, earlier payments sent untraced, the traced payment, one line of context)."""
    world = World(seed=42)
    if name == "legit":
        tx = world.legit_traffic("trace", 1, 1, 0.0, 60.0)[0][0]
        return world, [], tx, "An honest shopper's agent buying one item."
    *earlier, tx = [t for t, _ in world.fraud_traffic("trace", 1, "card_tester", 8, 0.0)]
    low, high = min(t.amount_cents for t in earlier), max(t.amount_cents for t in earlier)
    context = (f"The same agent already made {len(earlier)} payments of ${low / 100:.2f}-${high / 100:.2f} "
               f"in the last {tx.ts - earlier[0].ts:.0f} s: testing stolen cards.")
    return world, earlier, tx, context


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", choices=["card-tester", "legit"], default="card-tester")
    parser.add_argument("--pace", type=float, default=0.3, help="seconds between printed hops")
    args = parser.parse_args(argv)
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ

    def paint(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    def action(value: str) -> str:
        return paint(value.upper(), "1;" + ACTION_COLORS[value])

    world, earlier, tx, context = scenario(args.scenario)
    hops: list[tuple[float, Hop]] = []

    def record(hop: Hop) -> None:
        if hop.payload.tx_id == tx.tx_id:
            hops.append((time.perf_counter(), hop))

    bureau = EngineBureau(DefensePipeline(identities=world.identities), port=free_port(), on_hop=record)
    bureau.start()
    try:
        for t in earlier:
            bureau.submit_sync(t)
        started = time.perf_counter()
        decision = bureau.submit_sync(tx)
    finally:
        bureau.stop()

    agents = [bureau.ingress, bureau.orchestrator, *bureau.specialists.values()]
    print(paint("One payment through the defense's uAgents", "1"))
    print(f"\n  Payment {tx.tx_id}: agent {tx.agent_id} pays {tx.merchant_id} ${tx.amount_cents / 100:.2f}")
    print(f"  {context}")
    print(f"\n  {len(agents)} Fetch.ai uAgents, each with its own address, talking only by message:")
    for agent in agents:
        print(f"    {agent.name:<20} {paint(agent.address[:12] + '...' + agent.address[-6:], '2')}")
    print()

    for at, hop in hops:
        arrow = f"{hop.sender:>20} ─{' ' + hop.message + ' ':─^17}─▶ {hop.receiver:<20}"
        elapsed = f"+{(at - started) * 1000:.1f} ms"
        print(f"  {paint(f'{elapsed:>9}', '2')}  {arrow}  {describe(hop.payload, action)}")
        time.sleep(args.pace)

    print(f"\n  Decision: {action(decision.action.value)}. {decision.explanation}")
    print(f"  {len(hops)} messages between {len(agents)} agents, "
          f"{(hops[-1][0] - started) * 1000:.1f} ms end to end.")


def describe(payload, action) -> str:
    if isinstance(payload, Transaction):
        return f"${payload.amount_cents / 100:.2f} to {payload.merchant_id}"
    if isinstance(payload, FeatureVector):
        return f"sees only its own {len(payload.values)} facts"
    if isinstance(payload, Vote):
        return f"{action(payload.vote.value)} {payload.score:.2f}  {', '.join(payload.rule_ids) or 'no rule fired'}"
    if isinstance(payload, Decision):
        veto = f", veto: {payload.vetoed_by}" if payload.vetoed_by else ""
        return f"{action(payload.action.value)}  weighted score {payload.weighted_score:.2f}{veto}"
    return ""


if __name__ == "__main__":
    main()

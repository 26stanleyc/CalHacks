"""Chat with the public agents over the real Agentverse mailbox, the way ASI:One does (M8).

    uv run python -m integrations.asi_one.mailbox_check --out evidence/m08/mailbox-transcript.md

A separate uAgent with its own mailbox sends the M8 prompts to the running Fraud Desk and
Merchant Scanner one at a time, waits for each reply, and writes a transcript. Nothing is
called in process: every message goes out to Agentverse and back, so a reply proves the
seed identity, both mailboxes, the Chat Protocol and the engine behind the agent.

The prompts follow the demo: check a payment, ask why *that* payment was blocked (its id is
read from the first reply), ask how the defense is doing, ask to run a round (this sender
is not on the allowlist, so it must be refused), scan a real product page, then the
intent-to-action flow: buy that page's item within budget (a review card must come back),
approve it the way a card click does (it settles through x402 on Base Sepolia), and try
again over budget (blocked, no card).

The sender's seed is generated into `integrations/.local/` (gitignored), so its address is
stable on one machine and never on the allowlist unless someone puts it there.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity

from integrations.asi_one.agent_base import connect_mailbox, ensure_event_loop, local_seed
from integrations.asi_one.router import REFUSAL_TEAM_ONLY
from integrations.settings import agent_seed, engine_http_url, engine_source

NAME = "fraud-defense-mailbox-check"
PORT = 8104
REPLY_TIMEOUT_S = 180

SCAN_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
_TX_IN_REPLY = re.compile(r"\(tx ([^,\s)]+)")
_PURCHASE_IN_REPLY = re.compile(r"\b(buy-[0-9a-f]{12})\b")


@dataclass
class Step:
    target: str  # "fraud_desk" or "merchant_scanner"
    prompt: str
    expect: str  # what a correct reply looks like, in words, for the transcript
    passed: object = None  # a callable(reply, card_kind) -> bool
    reply: str = ""
    card_kind: str | None = None  # the interactive card that came with the reply, if any
    seconds: float = 0.0
    ok: bool = False


@dataclass
class Run:
    steps: list[Step]
    addresses: dict[str, str]
    index: int = 0
    sent_at: float = 0.0
    log: list[str] = field(default_factory=list)


def address_of(seed_name: str) -> str:
    """The agent1q... address an agent with this seed runs at, without starting it."""
    seed = agent_seed(seed_name)
    if not seed:
        raise SystemExit(f"{seed_name.upper()}_AGENT_SEED is not set in .env")
    return Identity.from_seed(seed, 0).address


def m8_steps() -> list[Step]:
    return [
        Step("fraud_desk", "Check this payment: agent a-1 paying merchant m-7 $49 for a USB-C charger, mandate under $30",
             "a real decision: Blocked, the mandate's over-cap veto, the specialists that objected",
             lambda r, c: r.startswith("Blocked") and "mandate" in r.lower()),
        Step("fraud_desk", "Why was {tx_id} blocked?",
             "the stored decision for that same payment, with its votes and rules",
             lambda r, c: "no record" not in r and ("Blocked" in r or "block" in r.lower())),
        Step("fraud_desk", "How is the defense doing?",
             "the engine's latest round metrics, both loss lines",
             lambda r, c: "loss" in r.lower()),
        Step("fraud_desk", "Run a round",
             "refused: this sender is not in TEAM_ALLOWLIST_ADDRESSES",
             lambda r, c: r == REFUSAL_TEAM_ONLY),
        Step("merchant_scanner", f"Is {SCAN_URL} safe for my shopping agent?",
             "a live page scan with an injection score",
             lambda r, c: "injection" in r.lower() or "score" in r.lower()),
        Step("fraud_desk", f"Buy {SCAN_URL} for me, budget $60",
             "page scanned, payment checked at the page's price, and a review card to approve",
             lambda r, c: c == "review" and bool(_PURCHASE_IN_REPLY.search(r))),
        Step("fraud_desk", '{{"action": "approve_purchase", "purchase_id": "{purchase_id}"}}',
             "the card's Approve click settles through x402 on Base Sepolia, with a basescan link",
             lambda r, c: r.startswith("Done.") and "basescan.org/tx/0x" in r),
        Step("fraud_desk", f"Buy {SCAN_URL} for me, budget $30",
             "over budget: blocked by the mandate veto, no card, nothing charged",
             lambda r, c: r.startswith("Blocked") and c is None and "Nothing was charged" in r),
    ]


def build_agent(run: Run, out: Path) -> Agent:
    ensure_event_loop()
    seed = agent_seed("mailbox_check") or local_seed("mailbox_check")
    agent = Agent(name=NAME, seed=seed, port=PORT, mailbox=True)
    protocol = Protocol(spec=chat_protocol_spec)

    async def send_current(ctx: Context) -> None:
        step = run.steps[run.index]
        if "{tx_id}" in step.prompt or "{purchase_id}" in step.prompt:
            earlier = "\n".join(s.reply for s in run.steps[: run.index])
            tx_id, purchase_id = _TX_IN_REPLY.search(earlier), _PURCHASE_IN_REPLY.findall(earlier)
            step.prompt = step.prompt.format(tx_id=tx_id.group(1) if tx_id else "tx-1234",
                                             purchase_id=purchase_id[-1] if purchase_id else "buy-none")
        run.sent_at = time.monotonic()
        ctx.logger.info("-> %s: %s", step.target, step.prompt)
        await ctx.send(run.addresses[step.target], ChatMessage(content=[TextContent(text=step.prompt)]))

    async def advance(ctx: Context) -> None:
        run.index += 1
        if run.index < len(run.steps):
            await send_current(ctx)
            return
        write_transcript(run, agent.address, out)
        passed = sum(s.ok for s in run.steps)
        ctx.logger.info("%d/%d replies as expected; transcript at %s", passed, len(run.steps), out)
        os._exit(0 if passed == len(run.steps) else 1)

    @protocol.on_message(ChatMessage)
    async def on_reply(ctx: Context, sender: str, msg: ChatMessage) -> None:
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        if run.index >= len(run.steps):
            return
        step = run.steps[run.index]
        if sender != run.addresses[step.target] or not msg.text().strip():
            return
        step.reply = msg.text().strip()
        step.card_kind = next((c.metadata.get("card_kind") for c in msg.content if isinstance(c, MetadataContent)
                               and c.metadata.get("card_protocol_version") == "1"), None)
        if step.card_kind:  # check the card parses, as ASI:One would
            card = next(c for c in msg.content if isinstance(c, MetadataContent))
            json.loads(card.metadata["card_payload"])
        step.seconds = time.monotonic() - run.sent_at
        step.ok = bool(step.passed(step.reply, step.card_kind))
        ctx.logger.info("<- %s (%.1fs, %s%s): %s", step.target, step.seconds, "ok" if step.ok else "UNEXPECTED",
                        f", {step.card_kind} card" if step.card_kind else "", step.reply.splitlines()[0])
        await advance(ctx)

    @protocol.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.debug("ack from %s", sender)

    @agent.on_event("startup")
    async def start(ctx: Context) -> None:
        ctx.logger.info("Mailbox check sender is %s (not on the allowlist)", agent.address)
        await connect_mailbox(agent, ctx)
        await asyncio.sleep(5)  # let the new mailbox settle before the first reply comes back
        await send_current(ctx)

    @agent.on_interval(period=5.0)
    async def watchdog(ctx: Context) -> None:
        if run.sent_at and run.index < len(run.steps) and time.monotonic() - run.sent_at > REPLY_TIMEOUT_S:
            step = run.steps[run.index]
            step.reply, step.seconds = f"(no reply within {REPLY_TIMEOUT_S}s)", REPLY_TIMEOUT_S
            ctx.logger.error("No reply from %s within %ds", step.target, REPLY_TIMEOUT_S)
            await advance(ctx)

    agent.include(protocol)
    return agent


def write_transcript(run: Run, sender: str, out: Path) -> None:
    source = engine_source()
    lines = [
        "# M8: the public agents over the real Agentverse mailbox",
        "",
        f"Written by `uv run python -m integrations.asi_one.mailbox_check` at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.",
        "",
        "Every prompt went from a separate uAgent through Agentverse to the running agent and back.",
        "",
        f"- sender (not allowlisted): `{sender}`",
        f"- fraud-desk: `{run.addresses['fraud_desk']}`",
        f"- merchant-scanner: `{run.addresses['merchant_scanner']}`",
        f"- engine behind the agents: `ENGINE_SOURCE={source}`"
        + (f" at {engine_http_url()}" if source == "http" else ""),
        f"- {sum(s.ok for s in run.steps)}/{len(run.steps)} replies as expected",
        "",
    ]
    for n, step in enumerate(run.steps, 1):
        lines += [
            f"## {n}. {step.target}: {step.prompt}",
            "",
            f"Expected: {step.expect}. **{'As expected' if step.ok else 'NOT as expected'}**, "
            f"reply after {step.seconds:.1f}s"
            + (f", with an interactive `{step.card_kind}` card." if step.card_kind else "."),
            "",
            "```text",
            step.reply,
            "```",
            "",
        ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("evidence/m08/mailbox-transcript.md"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    addresses = {name: address_of(name) for name in ("fraud_desk", "merchant_scanner")}
    build_agent(Run(steps=m8_steps(), addresses=addresses), args.out).run()


if __name__ == "__main__":
    main()

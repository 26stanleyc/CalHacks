"""Building a public chat agent: uAgents Chat Protocol + an Agentverse mailbox.

Shared by the hello agent and both public agents (spec 3.7). What it sets up:

- `Agent(mailbox=True)`, so ASI:One reaches the agent through Agentverse and the process
  needs no public URL. Identity comes from a seed in `.env`, so the address is stable.
- `Protocol(spec=chat_protocol_spec)` (AgentChatProtocol 0.3.0), with the acknowledgement
  every incoming `ChatMessage` is owed, and a reply closed by `EndSessionContent`.
- Mailbox registration on startup from `AGENTVERSE_API_KEY`, the same call the local
  agent inspector's "Connect" button makes, so there is nothing to click by hand.

The answer itself comes from a `respond(sender, text) -> str` callback. It runs in a
worker thread: the routing LLM call is blocking, and the agent's event loop has to stay
free to acknowledge and answer other messages.
"""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    StartSessionContent,
    TextContent,
    chat_protocol_spec,
)

from integrations.settings import agent_seed, agentverse_api_key

logger = logging.getLogger(__name__)

# ASI:One renders long replies poorly, and our answers are short by design.
MAX_REPLY_CHARS = 3_000

Responder = Callable[[str, str], str]


def build_chat_agent(
    *,
    name: str,
    seed_name: str,
    port: int,
    respond: Responder,
    description: str,
    readme: str | None = None,
    starter_prompts: list[str] | None = None,
    local_seed_fallback: bool = False,
) -> Agent:
    """A mailbox-connected chat agent that answers with `respond(sender, text)`.

    `seed_name` names the env var: "fraud_desk" reads FRAUD_DESK_AGENT_SEED. The seed fixes
    the agent's address, so Agentverse keeps listing the same agent across restarts.
    `local_seed_fallback` generates and stores one locally instead of failing, which is
    only right for the throwaway hello agent.
    """
    seed = agent_seed(seed_name) or (local_seed(seed_name) if local_seed_fallback else "")
    if not seed:
        raise RuntimeError(
            f"{seed_name.upper()}_AGENT_SEED is not set. Without it the agent's address would "
            "change on every start and Agentverse would list a new agent each time."
        )

    ensure_event_loop()
    agent = Agent(
        name=name,
        seed=seed,
        port=port,
        mailbox=True,
        publish_agent_details=True,
        description=description,
        metadata={"categories": ["finance", "security"], "is_public": "True"},
    )

    protocol = Protocol(spec=chat_protocol_spec)

    @protocol.on_message(ChatMessage)
    async def handle_chat(ctx: Context, sender: str, msg: ChatMessage) -> None:
        # Acknowledge first: the sender should not wait on our model call for it.
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))

        if any(isinstance(item, StartSessionContent) for item in msg.content) and not msg.text().strip():
            return  # session opener with no question in it

        question = msg.text()
        ctx.logger.info("Message from %s (%d chars)", sender, len(question))
        try:
            answer = await asyncio.to_thread(respond, sender, question)
        except Exception:
            ctx.logger.exception("Responder failed")
            answer = "Something went wrong on my side. Please try that again."

        await ctx.send(
            sender,
            ChatMessage(content=[TextContent(text=_trim(answer)), EndSessionContent()]),
        )

    @protocol.on_message(ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        ctx.logger.debug("Ack from %s for %s", sender, msg.acknowledged_msg_id)

    @agent.on_event("startup")
    async def announce(ctx: Context) -> None:
        ctx.logger.info("%s is up at %s", name, agent.address)
        await connect_mailbox(agent, ctx, starter_prompts=starter_prompts, readme=readme)
        ctx.logger.info("Chat with it in ASI:One: https://asi1.ai (search for %r)", name)

    agent.include(protocol, publish_manifest=True)
    return agent


async def connect_mailbox(
    agent: Agent,
    ctx: Context,
    *,
    starter_prompts: list[str] | None = None,
    readme: str | None = None,
) -> bool:
    """Link this agent to the Agentverse mailbox with the account's API key.

    Without a key the agent still runs and can be connected by hand from the local
    inspector, so a missing key is a warning rather than an error.
    """
    key = agentverse_api_key()
    if not key:
        ctx.logger.warning(
            "AGENTVERSE_API_KEY is not set, so the mailbox was not linked. Open the inspector "
            "link above and press Connect to do it by hand."
        )
        return False

    from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
    from uagents_core.registration import RegistrationRequest

    from uagents_core.registration import AgentProfile

    # Built here rather than taken from the agent, because `Agent` only picks up a readme
    # from a file path and has no field for starter prompts. These are what Agentverse and
    # ASI:One show on the agent's page.
    profile = AgentProfile(
        description=agent._description or "",
        readme=readme or "",
        starter_prompts=starter_prompts or [],
    )

    details = RegistrationRequest(
        address=agent.address,
        name=agent.name,
        profile=profile,
        endpoints=agent._endpoints,
        protocols=list(agent.protocols.keys()),
        metadata=agent.metadata,
    )
    try:
        result = await register_in_agentverse(
            request=AgentverseConnectRequest(user_token=key, agent_type="mailbox"),
            identity=agent._identity,
            prefix=agent._prefix,
            agentverse=agent._agentverse,
            agent_details=details,
        )
    except Exception:
        ctx.logger.exception("Mailbox registration raised; connect by hand from the inspector")
        return False

    if getattr(result, "success", False):
        ctx.logger.info("Mailbox linked; the agent is reachable from ASI:One")
        return True
    ctx.logger.warning("Mailbox registration failed: %s", getattr(result, "detail", result))
    return False


def ensure_event_loop() -> None:
    """Give `Agent` a current event loop to adopt.

    uAgents 0.26 reads `asyncio.get_event_loop()` in `Agent.__init__`. Up to Python 3.13
    that created a loop on demand; on 3.14 it raises when none is set, which this venv is.
    Creating one here is what the older Python did implicitly, and `agent.run()` then runs
    on it. Anything else that builds a uAgent (a Bureau, for one) needs the same two lines.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return  # already inside a loop; Agent will pick that one up
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def local_seed(seed_name: str) -> str:
    """A generated seed kept in `integrations/.local/`, which is gitignored.

    Only for the hello agent: a seed committed to the repo would let anyone run an agent
    at the same address, and a fresh random one each start would litter Agentverse.
    """
    import secrets

    path = Path(__file__).resolve().parent.parent / ".local" / f"{seed_name}.seed"
    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = f"{seed_name}-{secrets.token_hex(24)}"
    path.write_text(seed + "\n")
    path.chmod(0o600)
    logger.info("Generated a local seed for %r at %s (gitignored)", seed_name, path)
    return seed


def _trim(answer: str) -> str:
    text = (answer or "").strip() or "I don't have an answer for that."
    if len(text) <= MAX_REPLY_CHARS:
        return text
    return text[: MAX_REPLY_CHARS - 3].rstrip() + "..."

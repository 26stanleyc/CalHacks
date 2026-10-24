"""The M0 smoke-test agent: proves the Agentverse mailbox and ASI:One path work.

No engine, no LLM, no fraud logic. It answers from a fixed script, so when it replies in
ASI:One the only things that can have worked are the seed identity, the mailbox link and
the Chat Protocol. That is exactly what the M0 manual check needs (screenshot into
`evidence/m0/`).

    uv run python -m integrations.asi_one.hello_agent
"""

import logging

from integrations.asi_one.agent_base import build_chat_agent

NAME = "fraud-defense-hello"
PORT = 8101

DESCRIPTION = "Smoke-test agent for a self-red-teaming fraud defense for agentic x402 payments."

README = """# Fraud Defense Hello

A connectivity check for our CalHacks project: a self-red-teaming, multi-agent fraud
defense that sits at the Coinbase x402 payment facilitator.

Say hello and it will answer. The agents that do the real work are the **Fraud Desk**
(check a payment, explain a decision, report how the defense is doing) and the
**Merchant Scanner** (is this product page safe for my shopping agent?).
"""

STARTER_PROMPTS = ["Hello", "What is this project?", "Are you alive?"]


def respond(sender: str, text: str) -> str:
    """A canned reply. The incoming text is only ever inspected, never followed."""
    lowered = (text or "").strip().lower()
    if any(word in lowered for word in ("what", "who", "project", "do you do", "about")):
        return (
            "I'm the smoke-test agent for a self-red-teaming fraud defense for agentic payments. "
            "It sits at the Coinbase x402 facilitator and protects both sides: stores from bad "
            "buyer agents, and buyer agents from stores that hide instructions in product pages.\n\n"
            "The working agents are the Fraud Desk and the Merchant Scanner."
        )
    return (
        "Hello from the fraud-defense team. I'm the hello agent, so the mailbox and chat "
        "protocol are working. Ask the Fraud Desk to check a payment, or the Merchant Scanner "
        "whether a product page is safe for your shopping agent."
    )


agent = build_chat_agent(
    name=NAME,
    seed_name="hello",
    local_seed_fallback=True,
    port=PORT,
    respond=respond,
    description=DESCRIPTION,
    readme=README,
    starter_prompts=STARTER_PROMPTS,
)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    agent.run()

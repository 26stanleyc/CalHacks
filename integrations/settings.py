"""Settings for the integration layer.

`contracts/config.py` owns the shared switches (`USE_MOCK_LLM`, `USE_STUB_ENGINE`, the
model assignment). This module owns only what is specific to Eric's integrations, so the
shared contract stays untouched.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def ensure_ca_bundle() -> None:
    """Point OpenSSL at certifi's CA bundle if nothing else has.

    python.org builds of Python on macOS ship without a CA bundle. `httpx` carries its own
    certifi, so Bitdeer and the x402 facilitator work regardless, but `aiohttp` (which
    uAgents uses) builds a default SSL context and fails every TLS handshake with
    "unable to get local issuer certificate" - which means no Agentverse mailbox.

    `scripts/fund_wallet.py` and `scripts/make_local_wallet.py` already do this for the CDP
    SDK; this is the same fix applied once for the whole package. Anything else that opens
    an aiohttp connection (a uAgents Bureau, for one) needs it too.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
    except ImportError:  # pragma: no cover - certifi is a dependency
        return
    os.environ["SSL_CERT_FILE"] = certifi.where()


ensure_ca_bundle()

# Bitdeer and the x402 facilitator both sit behind Cloudflare, which 403s the default
# Python user agent. Every outbound call from this package sends this header.
USER_AGENT = os.environ.get("INTEGRATIONS_USER_AGENT", "calhacks-fraud-defense/0.1 (+integrations)")

# Base Sepolia, the only network this package will ever settle on (spec 2, M13).
BASE_SEPOLIA_CAIP2 = "eip155:84532"
BASE_SEPOLIA_V1_NAME = "base-sepolia"
BASE_SEPOLIA_EXPLORER = "https://sepolia.basescan.org"

# USDC has 6 decimals and our money is integer cents, so one cent is 10_000 base units.
USDC_UNITS_PER_CENT = 10_000


def flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# --- Engine source (spec 8.4 rule 3; the I2 switch) ---------------------------------
#
# "http"      contracts.api.HttpEngine against ENGINE_HTTP_URL: the real engine served by
#             `uv run python -m engine.serve`. The default since I2.
# "inprocess" the real engine object, built in the agent's own process from engine/.
# "stub"      contracts.stubs.StubEngine (canned answers). Only when asked for by name.


def engine_source() -> str:
    """Which Engine the chat agents talk to.

    The default is HTTP to `engine.serve`, so the chat agents and the dashboard share one
    engine: a round run from the dashboard shows up in "how is the defense doing?", and a
    payment checked in chat can be explained from the dashboard. The stub is never chosen
    by default any more, because canned answers in a judge's chat look like real ones.
    """
    explicit = os.environ.get("ENGINE_SOURCE", "").strip().lower()
    if explicit:
        return explicit
    return "http"


def engine_http_url() -> str:
    return os.environ.get("ENGINE_HTTP_URL", "http://localhost:8000")


# --- x402 settlement ----------------------------------------------------------------
#
# "stub" never touches the network (used for the precompute runs, spec 4.1).
# "real" signs with the testnet buyer wallet and settles through the facilitator.


def x402_mode() -> str:
    explicit = os.environ.get("X402_MODE", "").strip().lower()
    if explicit:
        return explicit
    has_wallet = bool(os.environ.get("TESTNET_WALLET_PRIVATE_KEY") and os.environ.get("MERCHANT_WALLET_ADDRESS"))
    return "real" if has_wallet else "stub"


def facilitator_url() -> str:
    return os.environ.get("X402_FACILITATOR_URL", "https://x402.org/facilitator").rstrip("/")


def max_settle_cents() -> int:
    """Refuse to settle more than this in one payment.

    A guard on a faucet-funded wallet, not a risk control: the engine decides what settles.
    """
    return int(os.environ.get("X402_MAX_SETTLE_CENTS", "100"))


# --- Chat agents (spec 3.7) ---------------------------------------------------------


def chat_allowlist() -> set[str]:
    """Senders allowed to call `run_round` and `set_freeze` from chat.

    Only TEAM_ALLOWLIST_ADDRESSES, and it may be empty. Deliberately *not*
    `contracts.api.team_allowlist()`, which also admits the literal sender "dashboard":
    that is right for the local HTTP bridge and wrong for a public chat agent.
    """
    raw = os.environ.get("TEAM_ALLOWLIST_ADDRESSES", "")
    return {a.strip() for a in raw.split(",") if a.strip()}


def agentverse_api_key() -> str:
    return os.environ.get("AGENTVERSE_API_KEY", "")


def agent_seed(name: str) -> str:
    """Seed for one public agent, e.g. agent_seed("fraud_desk") -> FRAUD_DESK_AGENT_SEED."""
    return os.environ.get(f"{name.upper()}_AGENT_SEED", "")

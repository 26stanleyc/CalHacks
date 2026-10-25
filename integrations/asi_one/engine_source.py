"""Where the chat agents get their `Engine`.

`ENGINE_SOURCE` in `.env` picks one of three, and the default is the real engine over HTTP:

    ENGINE_SOURCE=http          # the default: talk to `uv run python -m engine.serve`
    ENGINE_HTTP_URL=http://localhost:8000

    ENGINE_SOURCE=inprocess     # build the real engine inside the agent's own process

    ENGINE_SOURCE=stub          # contracts.stubs.StubEngine, canned answers (tests, offline work)

Nothing else in `integrations/` knows which of the three it got: they all satisfy
`contracts.entrypoints.Engine`.

The stub is only ever used when asked for by name. An agent that cannot get the engine it
was configured for says so loudly instead of quietly answering judges with canned data.
"""

import logging

from contracts.entrypoints import Engine
from integrations.settings import chat_allowlist, engine_http_url, engine_source

logger = logging.getLogger(__name__)

# Where the in-process engine lives. Kept as a (module, factory) list so a move needs no
# change anywhere else.
INPROCESS_FACTORIES = (
    ("engine.entrypoints", "build_engine"),
    ("engine.api", "build_engine"),
    ("engine", "build_engine"),
)


def get_engine(source: str | None = None) -> Engine:
    """The Engine the chat agents call. Raises if the configured one cannot be built."""
    chosen = (source or engine_source()).strip().lower()

    if chosen == "stub":
        return _stub_engine()

    if chosen == "http":
        from contracts.api import HttpEngine

        url = engine_http_url()
        logger.info("Chat agents will call the real engine over HTTP at %s", url)
        if not engine_reachable(url):
            # Not fatal: the engine may simply be starting up. Every answer until it is up says
            # "I can't reach the decision engine", never a canned decision.
            logger.error(
                "Nothing is answering at %s. Start the engine with `uv run python -m engine.serve`, "
                "or set ENGINE_SOURCE=inprocess to build it inside this process.",
                url,
            )
        return HttpEngine(url)

    if chosen == "inprocess":
        engine = _inprocess_engine()
        if engine is None:
            raise RuntimeError(
                "ENGINE_SOURCE=inprocess but the real engine could not be built (see the log above). "
                "Set ENGINE_SOURCE=stub to run on canned answers deliberately."
            )
        return engine

    raise ValueError(f"Unknown ENGINE_SOURCE={chosen!r}; use http, inprocess or stub")


def engine_reachable(url: str, timeout: float = 2.0) -> bool:
    """Whether something serves the engine's HTTP bridge at `url`."""
    import httpx

    try:
        return httpx.get(f"{url.rstrip('/')}/openapi.json", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def _stub_engine() -> Engine:
    from contracts.stubs import StubEngine

    logger.warning("Chat agents are running on contracts.stubs.StubEngine: every answer is canned")
    return StubEngine(allowlist=chat_allowlist())


def _inprocess_engine() -> Engine | None:
    """Import and build the real engine, if it is there. Never raises."""
    import importlib

    for module_name, factory_name in INPROCESS_FACTORIES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        factory = getattr(module, factory_name, None)
        if factory is None:
            continue
        try:
            engine = factory()
        except Exception:
            logger.exception("%s.%s() raised while building the engine", module_name, factory_name)
            continue
        logger.info("Chat agents are running on the in-process engine from %s.%s", module_name, factory_name)
        return engine
    return None

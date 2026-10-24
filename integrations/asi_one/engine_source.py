"""Where the chat agents get their `Engine`.

Stanley's engine does not exist yet, so the default is `contracts.stubs.StubEngine`.
At checkpoint I2 this becomes a one-line change in `.env`:

    ENGINE_SOURCE=http          # talk to his engine (or create_stub_app) over HTTP
    ENGINE_HTTP_URL=http://localhost:8000

    ENGINE_SOURCE=inprocess     # import the real engine into this process

Nothing else in `integrations/` knows which of the three it got: they all satisfy
`contracts.entrypoints.Engine`.
"""

import logging

from contracts.entrypoints import Engine
from integrations.settings import chat_allowlist, engine_http_url, engine_source

logger = logging.getLogger(__name__)

# Where an in-process engine is expected to live once Stanley's lands. Kept as a
# (module, factory) pair so this file needs no change when it appears.
INPROCESS_FACTORIES = (
    ("engine.entrypoints", "build_engine"),
    ("engine.api", "build_engine"),
    ("engine", "build_engine"),
)


def get_engine(source: str | None = None) -> Engine:
    """The Engine the chat agents call. Falls back to the stub rather than failing."""
    chosen = (source or engine_source()).strip().lower()

    if chosen == "stub":
        return _stub_engine()

    if chosen == "http":
        from contracts.api import HttpEngine

        url = engine_http_url()
        logger.info("Chat agents will call the engine over HTTP at %s", url)
        return HttpEngine(url)

    if chosen == "inprocess":
        engine = _inprocess_engine()
        if engine is not None:
            return engine
        logger.warning("ENGINE_SOURCE=inprocess but no engine factory was importable; using the stub engine")
        return _stub_engine()

    logger.warning("Unknown ENGINE_SOURCE=%r; using the stub engine", chosen)
    return _stub_engine()


def _stub_engine() -> Engine:
    from contracts.stubs import StubEngine

    logger.info("Chat agents are running on contracts.stubs.StubEngine (canned answers)")
    return StubEngine(allowlist=chat_allowlist())


def _inprocess_engine() -> Engine | None:
    """Import the real engine, if it is there. Never raises."""
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

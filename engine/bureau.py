"""The engine's uAgents, all in one Bureau process (spec 3.4).

    ingress --SubmitTx--> orchestrator --ScoreRequest--> each specialist
                          orchestrator <--VoteReply----- each specialist
    ingress <--DecisionReply-- orchestrator

The agents call the same DefensePipeline objects as the in-process path, so decisions are
identical either way.

Kept internal: agents have no endpoints (nothing registers on the Almanac), the Bureau's HTTP
server binds to 127.0.0.1 only, every handler drops messages that don't come from the expected
internal agent, and agent seeds are random per process unless ENGINE_AGENT_SEED_PREFIX is set.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass

import uagents.asgi
from pydantic import BaseModel
from uagents import Agent, Bureau, Context, Model

from contracts.schemas import Decision, FeatureVector, Lane, Transaction, Vote
from engine.messages import DecisionReply, ScoreRequest, SubmitTx, VoteReply
from engine.pipeline import DefensePipeline
from engine.store import LogStore, log_bundle

DEFAULT_BUREAU_PORT = 8100  # contracts/api.py uses 8000
log = logging.getLogger(__name__)


def _agent(name: str, seed_prefix: str, loop: asyncio.AbstractEventLoop) -> Agent:
    return Agent(
        name=name,
        seed=f"{seed_prefix}:{name}",
        loop=loop,  # Python 3.14 has no implicit event loop, so Agent() must be given one
        enable_agent_inspector=False,
        publish_agent_details=False,
        report_events=False,
        mark_inactive_on_shutdown=False,
        log_level=logging.ERROR,  # silences the expected "no endpoints, skipping registration" warning
    )


@dataclass(frozen=True)
class Hop:
    """One message delivered between two of the engine's agents, reported to `on_hop` as it
    arrives (engine/trace.py prints them). `payload` is the contracts model the message carried."""

    sender: str
    receiver: str
    message: str
    payload: BaseModel


class EngineBureau:
    """Owns the agents and the Bureau: `start()`, then `submit_sync(tx)` / `submit_with_features(tx)`
    (or `await submit(tx)`), then `stop()`. If a store is given, every decision is logged.
    Submissions are expected one at a time per tx_id (FraudEngine serializes them)."""

    def __init__(self, pipeline: DefensePipeline, store: LogStore | None = None, port: int | None = None,
                 seed_prefix: str | None = None, on_hop: Callable[[Hop], None] | None = None):
        self.pipeline = pipeline
        self.store = store
        self.on_hop = on_hop
        self.lanes: tuple[Lane, ...] = pipeline.lanes
        self.port = port or int(os.environ.get("ENGINE_BUREAU_PORT", DEFAULT_BUREAU_PORT))
        prefix = seed_prefix or os.environ.get("ENGINE_AGENT_SEED_PREFIX") or secrets.token_hex(16)
        self._loop: asyncio.AbstractEventLoop | None = asyncio.new_event_loop()
        self.ingress = _agent("ingress", prefix, self._loop)
        self.orchestrator = _agent("orchestrator", prefix, self._loop)
        self.specialists = {lane: _agent(f"specialist-{lane.value}", prefix, self._loop) for lane in self.lanes}
        self._lane_of = {agent.address: lane for lane, agent in self.specialists.items()}
        self._names = {a.address: a.name for a in (self.ingress, self.orchestrator, *self.specialists.values())}
        self._waiting: dict[str, concurrent.futures.Future[tuple[Decision, FeatureVector]]] = {}
        self._pending: dict[str, tuple[Transaction, FeatureVector, dict[str, Vote]]] = {}
        self._features: dict[str, FeatureVector] = {}
        self._ingress_ctx: Context | None = None
        self._ready = threading.Event()
        self._bureau: Bureau | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._wire()

    def _wire(self) -> None:
        @self.ingress.on_event("startup")
        async def remember_context(ctx: Context):
            self._ingress_ctx = ctx
            self._ready.set()

        @self.ingress.on_message(model=DecisionReply)
        async def resolve(ctx: Context, sender: str, msg: DecisionReply):
            if sender != self.orchestrator.address:
                log.warning("dropped a DecisionReply from unexpected sender %s", sender)
                return
            decision = Decision.model_validate_json(msg.payload)
            self._hop(sender, self.ingress, msg, decision)
            future = self._waiting.pop(decision.tx_id, None)
            features = self._features.pop(decision.tx_id, None)
            if future is not None and not future.done() and features is not None:
                future.set_result((decision, features))

        @self.orchestrator.on_message(model=SubmitTx)
        async def fan_out(ctx: Context, sender: str, msg: SubmitTx):
            if sender != self.ingress.address:
                log.warning("dropped a SubmitTx from unexpected sender %s", sender)
                return
            tx = Transaction.model_validate_json(msg.payload)
            self._hop(sender, self.orchestrator, msg, tx)
            features = self.pipeline.featurize(tx, commit=msg.commit)
            self._pending[tx.tx_id] = (tx, features, {})
            for lane, agent in self.specialists.items():
                lane_features = self.pipeline.lane_slice(lane, features)
                await ctx.send(agent.address, ScoreRequest(tx=msg.payload, features=lane_features.model_dump_json()))

        @self.orchestrator.on_message(model=VoteReply)
        async def collect(ctx: Context, sender: str, msg: VoteReply):
            lane = self._lane_of.get(sender)
            vote = Vote.model_validate_json(msg.payload)
            if lane is None or vote.voter != lane.value:
                log.warning("dropped a VoteReply from unexpected sender %s", sender)
                return
            self._hop(sender, self.orchestrator, msg, vote)
            pending = self._pending.get(vote.tx_id)
            if pending is None:
                return
            tx, features, votes = pending
            votes.setdefault(vote.voter, vote)  # one vote per specialist; duplicates are ignored
            if len(votes) < len(self.lanes):
                return
            del self._pending[vote.tx_id]
            decision = self.pipeline.combine(tx, list(votes.values()), features)
            if self.store is not None:
                await asyncio.to_thread(log_bundle, self.store, tx, features, decision)
            self._features[tx.tx_id] = features
            await ctx.send(self.ingress.address, DecisionReply(payload=decision.model_dump_json()))

        for lane, agent in self.specialists.items():
            self._wire_specialist(lane, agent)

    def _wire_specialist(self, lane: Lane, agent: Agent) -> None:
        @agent.on_message(model=ScoreRequest)
        async def score(ctx: Context, sender: str, msg: ScoreRequest):
            if sender != self.orchestrator.address:
                log.warning("dropped a ScoreRequest from unexpected sender %s", sender)
                return
            tx = Transaction.model_validate_json(msg.tx)
            features = FeatureVector.model_validate_json(msg.features)
            self._hop(sender, agent, msg, features)
            vote = self.pipeline.specialists[lane].vote(tx, features)
            await ctx.send(sender, VoteReply(payload=vote.model_dump_json()))

    def _hop(self, sender: str, receiver: Agent, msg: Model, payload: BaseModel) -> None:
        if self.on_hop is not None:
            self.on_hop(Hop(self._names[sender], receiver.name, type(msg).__name__, payload))

    def start(self) -> None:
        """Run the Bureau on its own event loop in a background thread. On shutdown the Bureau
        cancels every task on its loop, so it must not share a loop with the caller."""
        uagents.asgi.HOST = "127.0.0.1"  # read when the server starts; never listen off-host
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("an EngineBureau can only be started once; create a new one")
        agents = [self.ingress, self.orchestrator, *self.specialists.values()]
        self._bureau = Bureau(agents=agents, port=self.port, loop=self._loop, log_level=logging.ERROR, shutdown_timeout=2)

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            self._task = self._loop.create_task(self._bureau.run_async())
            with contextlib.suppress(asyncio.CancelledError):
                self._loop.run_until_complete(self._task)

        self._thread = threading.Thread(target=run, name="engine-bureau", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10) or not self._thread.is_alive():
            self.stop()
            raise RuntimeError(f"the Bureau's agents did not start (is port {self.port} free?)")

    def submit_with_features(self, tx: Transaction, timeout: float = 5.0, commit: bool = True) -> tuple[Decision, FeatureVector]:
        if self._ingress_ctx is None or self._loop is None:
            raise RuntimeError("call start() first")
        future: concurrent.futures.Future[tuple[Decision, FeatureVector]] = concurrent.futures.Future()
        self._waiting[tx.tx_id] = future
        send = self._ingress_ctx.send(self.orchestrator.address, SubmitTx(payload=tx.model_dump_json(), commit=commit))
        asyncio.run_coroutine_threadsafe(send, self._loop)
        try:
            return future.result(timeout)
        finally:
            self._waiting.pop(tx.tx_id, None)

    def submit_sync(self, tx: Transaction, timeout: float = 5.0) -> Decision:
        return self.submit_with_features(tx, timeout)[0]

    async def submit(self, tx: Transaction, timeout: float = 5.0) -> Decision:
        return await asyncio.to_thread(self.submit_sync, tx, timeout)

    def stop(self) -> None:
        """Ask the Bureau's HTTP server to exit (which releases the port), then wait for it."""
        if self._loop is None:
            return
        server = getattr(getattr(self._bureau, "_server", None), "_server", None)
        if server is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(setattr, server, "should_exit", True)
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive() and self._task is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._task.cancel)
                self._thread.join(timeout=5)
        if not self._loop.is_closed() and not self._loop.is_running():
            self._loop.close()
        self._task = None
        self._thread = None
        self._loop = None

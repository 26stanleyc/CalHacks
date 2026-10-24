"""The engine's uAgents, all in one Bureau process (spec 3.4).

    ingress --SubmitTx--> orchestrator --ScoreRequest--> each specialist
                          orchestrator <--VoteReply----- each specialist
    ingress <--DecisionReply-- orchestrator (after logging tx, votes and decision)

Agents have no endpoints, so nothing registers on the Almanac or leaves this process.
"""

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import threading
import time

from uagents import Agent, Bureau, Context

from contracts.schemas import Decision, FeatureVector, Lane, Transaction, Vote
from engine.defense import S1_LANES, placeholder_decide, placeholder_features, placeholder_vote
from engine.messages import DecisionReply, ScoreRequest, SubmitTx, VoteReply
from engine.store import LogStore

DEFAULT_BUREAU_PORT = 8100  # contracts/api.py uses 8000


def _agent(name: str, seed_prefix: str) -> Agent:
    return Agent(
        name=name,
        seed=f"{seed_prefix}:{name}",
        enable_agent_inspector=False,
        publish_agent_details=False,
        report_events=False,
        mark_inactive_on_shutdown=False,
        log_level=logging.WARNING,
    )


class EngineBureau:
    """Owns the agents and the Bureau: `start()`, then `submit_sync(tx)` or `await submit(tx)`, then `stop()`."""

    def __init__(self, store: LogStore, lanes: tuple[Lane, ...] = S1_LANES, port: int | None = None,
                 seed_prefix: str | None = None):
        self.store = store
        self.lanes = lanes
        self.port = port or int(os.environ.get("ENGINE_BUREAU_PORT", DEFAULT_BUREAU_PORT))
        prefix = seed_prefix or os.environ.get("ENGINE_AGENT_SEED_PREFIX", "calhacks-engine-dev")
        self.ingress = _agent("ingress", prefix)
        self.orchestrator = _agent("orchestrator", prefix)
        self.specialists = {lane: _agent(f"specialist-{lane.value}", prefix) for lane in lanes}
        self._queue: asyncio.Queue[Transaction] = asyncio.Queue()
        self._waiting: dict[str, concurrent.futures.Future[Decision]] = {}
        self._pending: dict[str, tuple[Transaction, list[Vote]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._task: asyncio.Task | None = None
        self._wire()

    def _wire(self) -> None:
        @self.ingress.on_interval(period=0.05)
        async def forward_submissions(ctx: Context):
            while not self._queue.empty():
                tx = self._queue.get_nowait()
                await ctx.send(self.orchestrator.address, SubmitTx(payload=tx.model_dump_json()))

        @self.ingress.on_message(model=DecisionReply)
        async def resolve(ctx: Context, sender: str, msg: DecisionReply):
            decision = Decision.model_validate_json(msg.payload)
            future = self._waiting.pop(decision.tx_id, None)
            if future is not None and not future.done():
                future.set_result(decision)

        @self.orchestrator.on_message(model=SubmitTx)
        async def fan_out(ctx: Context, sender: str, msg: SubmitTx):
            tx = Transaction.model_validate_json(msg.payload)
            await asyncio.to_thread(self.store.log_transaction, tx)
            self._pending[tx.tx_id] = (tx, [])
            features = placeholder_features(tx)
            for agent in self.specialists.values():
                await ctx.send(agent.address, ScoreRequest(tx=msg.payload, features=features.model_dump_json()))

        @self.orchestrator.on_message(model=VoteReply)
        async def collect(ctx: Context, sender: str, msg: VoteReply):
            vote = Vote.model_validate_json(msg.payload)
            tx, votes = self._pending.get(vote.tx_id, (None, None))
            if tx is None:
                return
            votes.append(vote)
            if len(votes) < len(self.lanes):
                return
            del self._pending[vote.tx_id]
            decision = placeholder_decide(tx, votes)
            await asyncio.to_thread(self.store.log_votes, votes)
            await asyncio.to_thread(self.store.log_decision, decision)
            await ctx.send(self.ingress.address, DecisionReply(payload=decision.model_dump_json()))

        for lane, agent in self.specialists.items():
            self._wire_specialist(lane, agent)

    def _wire_specialist(self, lane: Lane, agent: Agent) -> None:
        @agent.on_message(model=ScoreRequest)
        async def score(ctx: Context, sender: str, msg: ScoreRequest):
            tx = Transaction.model_validate_json(msg.tx)
            vote = placeholder_vote(lane, tx, FeatureVector.model_validate_json(msg.features))
            await ctx.send(sender, VoteReply(payload=vote.model_dump_json()))

    def start(self) -> None:
        """Run the Bureau on its own event loop in a background thread. On shutdown the Bureau
        cancels every task on its loop, so it must not share a loop with the caller."""
        self._loop = asyncio.new_event_loop()
        agents = [self.ingress, self.orchestrator, *self.specialists.values()]
        bureau = Bureau(agents=agents, port=self.port, loop=self._loop, log_level=logging.WARNING, shutdown_timeout=2)

        def run() -> None:
            asyncio.set_event_loop(self._loop)
            self._task = self._loop.create_task(bureau.run_async())
            with contextlib.suppress(asyncio.CancelledError):
                self._loop.run_until_complete(self._task)

        self._thread = threading.Thread(target=run, name="engine-bureau", daemon=True)
        self._thread.start()
        time.sleep(0.5)  # let agents finish startup and begin their interval tasks

    def submit_sync(self, tx: Transaction, timeout: float = 5.0) -> Decision:
        future: concurrent.futures.Future[Decision] = concurrent.futures.Future()
        self._waiting[tx.tx_id] = future
        self._loop.call_soon_threadsafe(self._queue.put_nowait, tx)
        try:
            return future.result(timeout)
        finally:
            self._waiting.pop(tx.tx_id, None)

    async def submit(self, tx: Transaction, timeout: float = 5.0) -> Decision:
        return await asyncio.to_thread(self.submit_sync, tx, timeout)

    def stop(self) -> None:
        if self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)
            self._thread.join(timeout=10)
            self._task = None

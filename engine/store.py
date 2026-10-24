"""Writes the engine's logs to the append-only tables in contracts/db.sql, and reads back what the
entry points need (explain, get_metrics).

`PostgresStore` is the real one; `MemoryStore` keeps the same records in memory for tests.
Rows are only ever inserted, never updated or deleted. Inside `with store.batch():` Postgres writes
are buffered and sent once per table at the end, so a 400-payment round costs a few round trips
to Supabase instead of thousands.
"""

import contextlib
import os
from typing import Iterator, Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from contracts.schemas import Decision, FeatureVector, GroundTruth, RoundMetrics, SettlementResult, Transaction, Vote


class LogStore(Protocol):
    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None: ...

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None: ...

    def log_features(self, features: FeatureVector) -> None: ...

    def log_votes(self, votes: list[Vote]) -> None: ...

    def log_decision(self, decision: Decision) -> None: ...

    def log_round_metrics(self, metrics: RoundMetrics) -> None: ...

    def log_settlement(self, result: SettlementResult) -> None: ...

    def get_decision(self, tx_id: str) -> Decision | None: ...

    def get_metrics(self, run_id: str, round: int | None = None) -> RoundMetrics | None: ...

    def batch(self) -> contextlib.AbstractContextManager: ...


def log_bundle(store: LogStore, tx: Transaction, features: FeatureVector, decision: Decision,
               truth: GroundTruth | None = None) -> None:
    """Everything one decision produces, in foreign-key order."""
    store.log_transaction(tx, truth)
    store.log_features(features)
    store.log_votes(decision.votes)
    store.log_decision(decision)


class MemoryStore:
    """Keeps deep copies, so callers can't change logged records and reads never return the
    caller's own object (tests compare against what was really logged)."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.transactions: list[Transaction] = []
        self.ground_truth: list[GroundTruth] = []
        self.features: list[FeatureVector] = []
        self.votes: list[Vote] = []
        self.decisions: list[Decision] = []
        self.metrics: list[RoundMetrics] = []
        self.settlements: list[SettlementResult] = []

    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None:
        self.runs.setdefault(run_id, {"seed": seed, "defense": defense, "config": config or {}})

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None:
        self.transactions.append(tx.model_copy(deep=True))
        if truth is not None:
            self.ground_truth.append(truth.model_copy(deep=True))

    def log_features(self, features: FeatureVector) -> None:
        self.features.append(features.model_copy(deep=True))

    def log_votes(self, votes: list[Vote]) -> None:
        self.votes.extend(v.model_copy(deep=True) for v in votes)

    def log_decision(self, decision: Decision) -> None:
        self.decisions.append(Decision.model_validate_json(decision.model_dump_json()))

    def log_round_metrics(self, metrics: RoundMetrics) -> None:
        self.metrics.append(metrics.model_copy(deep=True))

    def log_settlement(self, result: SettlementResult) -> None:
        self.settlements.append(result.model_copy(deep=True))

    def get_decision(self, tx_id: str) -> Decision | None:
        found = next((d for d in reversed(self.decisions) if d.tx_id == tx_id), None)
        return found.model_copy(deep=True) if found else None

    def get_metrics(self, run_id: str, round: int | None = None) -> RoundMetrics | None:
        rows = [m for m in self.metrics if m.run_id == run_id and (round is None or m.round == round)]
        found = max(rows, key=lambda m: m.round, default=None)
        return found.model_copy(deep=True) if found else None

    @contextlib.contextmanager
    def batch(self) -> Iterator[None]:
        yield


_INSERTS = {
    "runs": "insert into runs (run_id, seed, defense, config) values (%s, %s, %s, %s) on conflict (run_id) do nothing",
    "transactions": "insert into transactions (tx_id, run_id, round, wave, source, attacker_id, payload) values (%s, %s, %s, %s, %s, %s, %s)",
    "ground_truth": "insert into ground_truth (tx_id, is_fraud, attacker_id, breaches_mandate) values (%s, %s, %s, %s)",
    "features": "insert into features (tx_id, feature_values) values (%s, %s)",
    "votes": "insert into votes (tx_id, voter, payload) values (%s, %s, %s)",
    "decisions": "insert into decisions (tx_id, action, escalated, payload) values (%s, %s, %s, %s)",
    "settlements": "insert into settlements (tx_id, settled, tx_hash, network, reason) values (%s, %s, %s, %s, %s)",
    "round_metrics": "insert into round_metrics (run_id, round, payload) values (%s, %s, %s)",
}
_ORDER = list(_INSERTS)  # foreign-key order for flushing


class PostgresStore:
    """One autocommit connection. `schema` lets tests write to a throwaway schema instead of public."""

    def __init__(self, dsn: str | None = None, schema: str | None = None):
        self.conn = psycopg.connect(dsn or os.environ["DATABASE_URL"], autocommit=True)
        if schema:
            self.conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))
        self._buffer: dict[str, list[tuple]] | None = None

    def close(self) -> None:
        self.conn.close()

    def _insert(self, table: str, rows: list[tuple]) -> None:
        if not rows:
            return
        if self._buffer is not None:
            self._buffer.setdefault(table, []).extend(rows)
            return
        with self.conn.cursor() as cur:
            cur.executemany(_INSERTS[table], rows)

    @contextlib.contextmanager
    def batch(self) -> Iterator[None]:
        if self._buffer is not None:  # already batching: the outer batch flushes
            yield
            return
        self._buffer = {}
        try:
            yield
        finally:
            buffer, self._buffer = self._buffer, None
            with self.conn.transaction(), self.conn.cursor() as cur:
                for table in _ORDER:
                    if buffer.get(table):
                        cur.executemany(_INSERTS[table], buffer[table])

    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None:
        self._insert("runs", [(run_id, seed, defense, Jsonb(config or {}))])

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None:
        self._insert("transactions", [(tx.tx_id, tx.run_id, tx.round, tx.wave, tx.source.value, tx.attacker_id,
                                       Jsonb(tx.model_dump(mode="json")))])
        if truth is not None:
            self._insert("ground_truth", [(truth.tx_id, truth.is_fraud, truth.attacker_id, truth.breaches_mandate)])

    def log_features(self, features: FeatureVector) -> None:
        self._insert("features", [(features.tx_id, Jsonb(features.values))])

    def log_votes(self, votes: list[Vote]) -> None:
        self._insert("votes", [(v.tx_id, v.voter, Jsonb(v.model_dump(mode="json"))) for v in votes])

    def log_decision(self, decision: Decision) -> None:
        self._insert("decisions", [(decision.tx_id, decision.action.value, decision.escalated,
                                    Jsonb(decision.model_dump(mode="json")))])

    def log_settlement(self, result: SettlementResult) -> None:
        self._insert("settlements", [(result.tx_id, result.settled, result.tx_hash, result.network, result.reason)])

    def log_round_metrics(self, metrics: RoundMetrics) -> None:
        self._insert("round_metrics", [(metrics.run_id, metrics.round, Jsonb(metrics.model_dump(mode="json")))])

    def get_decision(self, tx_id: str) -> Decision | None:
        row = self.conn.execute("select payload from decisions where tx_id = %s", (tx_id,)).fetchone()
        return Decision.model_validate(row[0]) if row else None

    def get_metrics(self, run_id: str, round: int | None = None) -> RoundMetrics | None:
        if round is None:
            row = self.conn.execute("select payload from round_metrics where run_id = %s order by round desc limit 1",
                                    (run_id,)).fetchone()
        else:
            row = self.conn.execute("select payload from round_metrics where run_id = %s and round = %s",
                                    (run_id, round)).fetchone()
        return RoundMetrics.model_validate(row[0]) if row else None

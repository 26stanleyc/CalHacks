"""Writes the engine's logs to the append-only tables in contracts/db.sql.

`PostgresStore` is the real one; `MemoryStore` keeps the same records in lists for tests.
Rows are only ever inserted, never updated or deleted.
"""

import os
from typing import Protocol

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from contracts.schemas import Decision, GroundTruth, Transaction, Vote


class LogStore(Protocol):
    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None: ...

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None: ...

    def log_votes(self, votes: list[Vote]) -> None: ...

    def log_decision(self, decision: Decision) -> None: ...


class MemoryStore:
    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.transactions: list[Transaction] = []
        self.ground_truth: list[GroundTruth] = []
        self.votes: list[Vote] = []
        self.decisions: list[Decision] = []

    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None:
        self.runs[run_id] = {"seed": seed, "defense": defense, "config": config or {}}

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None:
        self.transactions.append(tx)
        if truth is not None:
            self.ground_truth.append(truth)

    def log_votes(self, votes: list[Vote]) -> None:
        self.votes.extend(votes)

    def log_decision(self, decision: Decision) -> None:
        self.decisions.append(decision)


class PostgresStore:
    """One autocommit connection. `schema` lets tests write to a throwaway schema instead of public."""

    def __init__(self, dsn: str | None = None, schema: str | None = None):
        self.conn = psycopg.connect(dsn or os.environ["DATABASE_URL"], autocommit=True)
        if schema:
            self.conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(schema)))

    def close(self) -> None:
        self.conn.close()

    def start_run(self, run_id: str, seed: int, defense: str = "full", config: dict | None = None) -> None:
        self.conn.execute(
            "insert into runs (run_id, seed, defense, config) values (%s, %s, %s, %s) on conflict (run_id) do nothing",
            (run_id, seed, defense, Jsonb(config or {})),
        )

    def log_transaction(self, tx: Transaction, truth: GroundTruth | None = None) -> None:
        self.conn.execute(
            "insert into transactions (tx_id, run_id, round, wave, source, attacker_id, payload)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            (tx.tx_id, tx.run_id, tx.round, tx.wave, tx.source.value, tx.attacker_id, Jsonb(tx.model_dump(mode="json"))),
        )
        if truth is not None:
            self.conn.execute(
                "insert into ground_truth (tx_id, is_fraud, attacker_id, breaches_mandate) values (%s, %s, %s, %s)",
                (truth.tx_id, truth.is_fraud, truth.attacker_id, truth.breaches_mandate),
            )

    def log_votes(self, votes: list[Vote]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "insert into votes (tx_id, voter, payload) values (%s, %s, %s)",
                [(v.tx_id, v.voter, Jsonb(v.model_dump(mode="json"))) for v in votes],
            )

    def log_decision(self, decision: Decision) -> None:
        self.conn.execute(
            "insert into decisions (tx_id, action, escalated, payload) values (%s, %s, %s, %s)",
            (decision.tx_id, decision.action.value, decision.escalated, Jsonb(decision.model_dump(mode="json"))),
        )

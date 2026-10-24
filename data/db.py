"""Connection helper for the shared Supabase Postgres at DATABASE_URL (read from the repo-root .env)."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_SQL = REPO_ROOT / "contracts" / "db.sql"

load_dotenv(REPO_ROOT / ".env")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set. Add it to the repo-root .env.")
    return url


def connect(autocommit: bool = True) -> psycopg.Connection:
    # prepare_threshold=None: Supabase's pooler can't keep server-side prepared statements.
    return psycopg.connect(database_url(), autocommit=autocommit, connect_timeout=15, prepare_threshold=None)

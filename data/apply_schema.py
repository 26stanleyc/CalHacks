"""Apply contracts/db.sql to the database at DATABASE_URL (R1).

Safe to re-run: every table and index uses "if not exists", so existing tables and rows are left alone.

    uv run python data/apply_schema.py
"""

import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db import DB_SQL, connect


def expected_tables() -> list[str]:
    return re.findall(r"create table if not exists (\w+)", DB_SQL.read_text())


def apply_schema(conn) -> list[str]:
    """Run db.sql and return the expected tables that now exist in the current schema."""
    conn.execute(DB_SQL.read_text())
    present = {
        row[0]
        for row in conn.execute(
            "select table_name from information_schema.tables where table_schema = current_schema()"
        ).fetchall()
    }
    return [t for t in expected_tables() if t in present]


def main() -> int:
    with connect() as conn:
        present = apply_schema(conn)
    missing = sorted(set(expected_tables()) - set(present))
    print(f"db.sql applied: {len(present)}/{len(expected_tables())} tables present")
    if missing:
        print("missing:", ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

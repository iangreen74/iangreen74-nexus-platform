"""Operator-runbook: apply one SQL migration file to overwatch-postgres,
recording the apply in a `schema_migrations` ledger so future automated
runners can pick up where manual applies left off.

Usage (from an ECS exec session inside the VPC — RDS SG blocks dev machines):
    python -m nexus.operator.db_apply_migration migrations/013_foo.sql

Behavior:
  - Ensures `schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ
    DEFAULT now(), checksum TEXT)` exists.
  - Refuses to re-apply a filename already recorded.
  - Reads the SQL file, runs it inside one transaction with the recording
    INSERT, so partial application can never leave the ledger out of sync.

NOT a runner. Operator picks the file. The eventual automated runner
(Phase 1.6, file separately) will read the same `schema_migrations` table
to know what's already been applied by hand.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from nexus.overwatch_v2.db import get_conn


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
)
"""


def apply_migration(path: str | Path) -> None:
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"migration file not found: {p}")
    sql = p.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    name = p.name
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_LEDGER_DDL)
        cur.execute("SELECT checksum FROM schema_migrations WHERE filename = %s",
                    (name,))
        row = cur.fetchone()
        if row is not None:
            raise SystemExit(
                f"already applied: {name} (checksum {row[0]}); refusing to re-apply"
            )
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (name, checksum),
        )
    print(f"applied: {name} (sha256 {checksum[:12]}…)")


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python -m nexus.operator.db_apply_migration <path>")
    apply_migration(args[0])


if __name__ == "__main__":
    main()

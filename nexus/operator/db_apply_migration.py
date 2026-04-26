"""Operator-runbook: apply one SQL migration file to overwatch-postgres,
recording the apply in a `schema_migrations` ledger so future automated
runners can pick up where manual applies left off.

Usage (from an ECS exec session inside the VPC — RDS SG blocks dev machines):
    python -m nexus.operator.db_apply_migration migrations/013_foo.sql

For one-shot ECS task wrappers that combine apply + post-apply verification,
import `apply_migration_idempotent` directly — it returns a structured
result instead of printing/SystemExit-ing, so the wrapper owns the
machine-readable output. See nexus/operator/db_apply_migration_with_verify.py.

Behavior:
  - Ensures `schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ
    DEFAULT now(), checksum TEXT)` exists.
  - Idempotent on a recorded filename whose checksum matches; refuses to
    proceed if the recorded checksum differs (drift detection).
  - Reads the SQL file, runs it inside one transaction with the recording
    INSERT, so partial application can never leave the ledger out of sync.

NOT a runner. Operator picks the file. The eventual automated runner
(Phase 1.6, file separately) will read the same `schema_migrations` table
to know what's already been applied by hand.
"""
from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.overwatch_v2.db import get_conn


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
)
"""

# Returned `status` values:
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED_MATCHING = "already_applied_matching"


class ChecksumMismatchError(RuntimeError):
    """schema_migrations records a different checksum for this filename —
    the file on disk drifted from what was applied. Operator must reconcile."""


def apply_migration_idempotent(path: str | Path) -> dict[str, Any]:
    """Apply if new, no-op if already applied with matching checksum, raise
    on drift. Returns:
      {"status": "applied" | "already_applied_matching",
       "filename": str, "checksum_sha256": str,
       "applied_at_utc": ISO8601 str | None}
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"migration file not found: {p}")
    sql = p.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    name = p.name
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(_LEDGER_DDL)
        cur.execute(
            "SELECT checksum, applied_at FROM schema_migrations WHERE filename = %s",
            (name,),
        )
        row = cur.fetchone()
        if row is not None:
            recorded_checksum, recorded_applied_at = row
            if recorded_checksum != checksum:
                raise ChecksumMismatchError(
                    f"{name}: recorded checksum {recorded_checksum} differs "
                    f"from file checksum {checksum}; reconcile before re-applying"
                )
            return {
                "status": STATUS_ALREADY_APPLIED_MATCHING,
                "filename": name,
                "checksum_sha256": checksum,
                "applied_at_utc": (
                    recorded_applied_at.astimezone(timezone.utc).isoformat()
                    if recorded_applied_at is not None else None
                ),
            }
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (name, checksum),
        )
    return {
        "status": STATUS_APPLIED,
        "filename": name,
        "checksum_sha256": checksum,
        "applied_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def apply_migration(path: str | Path) -> None:
    """CLI-friendly wrapper. Prints "applied: ..." or SystemExit on drift."""
    try:
        result = apply_migration_idempotent(path)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    except ChecksumMismatchError as e:
        raise SystemExit(f"drift detected: {e}")
    if result["status"] == STATUS_ALREADY_APPLIED_MATCHING:
        print(f"already applied (matching): {result['filename']} "
              f"(sha256 {result['checksum_sha256'][:12]}…)")
    else:
        print(f"applied: {result['filename']} "
              f"(sha256 {result['checksum_sha256'][:12]}…)")


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python -m nexus.operator.db_apply_migration <path>")
    apply_migration(args[0])


if __name__ == "__main__":
    main()

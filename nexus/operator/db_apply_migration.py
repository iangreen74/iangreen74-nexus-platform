"""Operator-runbook: apply one SQL migration file to a target Postgres,
recording the apply in a `schema_migrations` ledger so future automated
runners can pick up where manual applies left off.

VaultScaler runs two Postgres instances (see
``docs/v1_migration_substrate_findings.md``):

- **V1** — `nexus-ontology-postgres`. Connection from `DATABASE_URL` env
  (injected from secret `nexus/ontology/postgres/connection-XlBoLD`).
  Carries `classifier_proposals`, ontology objects, founder data.
- **V2** — `overwatch-postgres`. Connection composed from
  `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DBNAME` env (injected from
  `overwatch-v2/postgres-master`). Carries `approval_tokens`, operator
  features substrate, Overwatch operational state.

Both DBs use the same `schema_migrations` ledger shape but as separate
tables — applying a migration only marks it in the targeted ledger.

Usage (from an ECS task inside the VPC — RDS SG blocks dev machines):

    python -m nexus.operator.db_apply_migration <path> [--target=v1|v2]

`--target` defaults to `v2` to preserve existing V2 wrapper behaviour
(`db_apply_migration_with_verify.py`); explicit V1 callers must pass
`--target=v1`.

For one-shot ECS task wrappers that combine apply + post-apply
verification, import `apply_migration_idempotent` directly — it returns
a structured result instead of printing/SystemExit-ing, so the wrapper
owns the machine-readable output. See
`db_apply_migration_with_verify.py` (V2 only — V1 has no analogous
verification wrapper today).

Behavior:
  - Ensures `schema_migrations(filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ
    DEFAULT now(), checksum TEXT)` exists in the targeted DB.
  - Idempotent on a recorded filename whose checksum matches; refuses to
    proceed if the recorded checksum differs (drift detection).
  - Reads the SQL file, runs it inside one transaction with the recording
    INSERT, so partial application can never leave the ledger out of sync.

NOT a runner. Operator picks the file + target. The eventual automated
runner (Phase 1.6, file separately) will read the same
`schema_migrations` tables to know what's already been applied by hand.
"""
from __future__ import annotations

import hashlib
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from nexus.overwatch_v2.db import get_conn


_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
)
"""

_VALID_TARGETS = ("v1", "v2")


class V1DBNotConfiguredError(RuntimeError):
    """V1 path expects DATABASE_URL to be set in the environment.

    The V1 task definition (``infra/19-migration-apply-task-v1.yml``)
    injects it from secret ``nexus/ontology/postgres/connection-XlBoLD``.
    """


@contextmanager
def _get_v1_conn() -> Iterator[Any]:
    """Yield a psycopg2 connection to V1 (nexus-ontology-postgres).

    Reads ``DATABASE_URL`` from env (same pattern as
    ``nexus/ontology/postgres.py:_connect``). The V1 migration task def
    injects ``DATABASE_URL`` from the
    ``nexus/ontology/postgres/connection-XlBoLD`` secret.
    """
    import psycopg2
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise V1DBNotConfiguredError(
            "DATABASE_URL not set — V1 (nexus-ontology-postgres) requires "
            "DATABASE_URL injected from secret "
            "nexus/ontology/postgres/connection-XlBoLD; see "
            "infra/19-migration-apply-task-v1.yml"
        )
    conn = psycopg2.connect(url, connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _open_target_conn(target: str):
    """Return the open connection context manager for ``target``.

    Routes V2 through the module-level ``get_conn`` symbol so the
    existing V2 tests (which monkeypatch ``runner.get_conn``) keep
    working unchanged. Routes V1 through the new ``_get_v1_conn``.
    """
    if target == "v1":
        return _get_v1_conn()
    if target == "v2":
        return get_conn()
    raise ValueError(
        f"unknown migration target: {target!r}; "
        f"expected one of {_VALID_TARGETS}"
    )

# Returned `status` values:
STATUS_APPLIED = "applied"
STATUS_ALREADY_APPLIED_MATCHING = "already_applied_matching"


class ChecksumMismatchError(RuntimeError):
    """schema_migrations records a different checksum for this filename —
    the file on disk drifted from what was applied. Operator must reconcile."""


def apply_migration_idempotent(
    path: str | Path,
    *,
    target: str = "v2",
) -> dict[str, Any]:
    """Apply if new, no-op if already applied with matching checksum, raise
    on drift. Returns:
      {"status": "applied" | "already_applied_matching",
       "filename": str, "checksum_sha256": str, "target": "v1"|"v2",
       "applied_at_utc": ISO8601 str | None}

    ``target='v2'`` (default) preserves existing V2 wrapper behaviour
    so the verify wrapper at ``db_apply_migration_with_verify.py`` and
    its tests keep working unchanged. ``target='v1'`` routes to the V1
    Postgres (``nexus-ontology-postgres``).
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"migration file not found: {p}")
    sql = p.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    name = p.name
    with _open_target_conn(target) as conn, conn.cursor() as cur:
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
                "target": target,
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
        "target": target,
        "applied_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def apply_migration(path: str | Path, *, target: str = "v2") -> None:
    """CLI-friendly wrapper. Prints "applied: ..." or SystemExit on drift."""
    try:
        result = apply_migration_idempotent(path, target=target)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    except ChecksumMismatchError as e:
        raise SystemExit(f"drift detected: {e}")
    except ValueError as e:
        raise SystemExit(str(e))
    prefix = (
        "already applied (matching)" if result["status"] == STATUS_ALREADY_APPLIED_MATCHING
        else "applied"
    )
    print(
        f"{prefix}: {result['filename']} "
        f"(target {result['target']}, "
        f"sha256 {result['checksum_sha256'][:12]}…)"
    )


def _parse_args(args: list[str]) -> tuple[str, str]:
    """Return ``(path, target)``. SystemExit on bad usage or unknown target."""
    target = "v2"
    positional: list[str] = []
    for arg in args:
        if arg.startswith("--target="):
            target = arg.split("=", 1)[1]
        elif arg == "--target":
            raise SystemExit(
                "usage: --target=v1|v2 (use '=', not space-separated)"
            )
        else:
            positional.append(arg)
    if len(positional) != 1:
        raise SystemExit(
            "usage: python -m nexus.operator.db_apply_migration "
            "<path> [--target=v1|v2]"
        )
    if target not in _VALID_TARGETS:
        raise SystemExit(
            f"unknown migration target: {target!r}; "
            f"expected one of {_VALID_TARGETS}"
        )
    return positional[0], target


def main(argv: list[str] | None = None) -> None:
    args = list(argv if argv is not None else sys.argv[1:])
    path, target = _parse_args(args)
    apply_migration(path, target=target)


if __name__ == "__main__":
    main()

"""One-shot ECS task entry point: apply migration + 4 schema verifications +
smoke test, with a single structured-JSON line on stdout.

Used by the `aria-console-migration-apply` task def
(infra/overwatch-v2/18-migration-apply-task.yml). Operator launches via
`aws ecs run-task --task-definition aria-console-migration-apply`; ECS
streams stdout to /aws/ecs/aria-console-migration-apply; exit-zero ↔ all
five steps OK.

This script is the template for Phase 1.6's automated migration runner —
the runner will read schema_migrations to skip already-applied files and
invoke this same shape of verifier per migration.

Output (single JSON object on the FINAL stdout line):

  {
    "ok": true|false,
    "applied_at_utc": "<iso8601>" | null,
    "migration_filename": "013_approval_tokens_align_with_code.sql",
    "checksum_sha256": "<64 hex>",
    "apply_status": "applied" | "already_applied_matching",
    "steps": {
      "apply": {"ok": ..., "details": {...}},
      "verify_schema_migrations": {"ok": ..., "details": {...}},
      "verify_columns":            {"ok": ..., "details": {...}},
      "verify_fk_gone":            {"ok": ..., "details": {...}},
      "smoke_test":                {"ok": ..., "details": {...}}
    },
    "failed_step": null | "<step name>"
  }

All-or-nothing: any failing step → ok=false, exit code 1.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

from nexus.operator.db_apply_migration import (
    ChecksumMismatchError, apply_migration_idempotent,
)
from nexus.overwatch_v2.db import get_conn


# ---- Verifications --------------------------------------------------------

def _verify_schema_migrations_row(filename: str) -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT filename, applied_at, substr(checksum, 1, 12) "
            "FROM schema_migrations WHERE filename = %s",
            (filename,),
        )
        row = cur.fetchone()
    if row is None:
        return {"ok": False, "details": {"error": f"no row for {filename}"}}
    return {"ok": True, "details": {
        "filename": row[0],
        "applied_at_utc": row[1].isoformat() if row[1] else None,
        "checksum_sha12": row[2],
    }}


_REQUIRED_COLUMNS = {
    "proposal_hash": "text",
    "issuer": "text",
    "proposal_id": "text",  # was uuid pre-013
}


def _verify_approval_tokens_columns() -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'approval_tokens' "
            "AND column_name IN ('proposal_hash', 'issuer', 'proposal_id') "
            "ORDER BY column_name"
        )
        rows = cur.fetchall()
    actual = {name: dtype for name, dtype in rows}
    missing = [c for c in _REQUIRED_COLUMNS if c not in actual]
    wrong_type = [
        f"{c}={actual[c]} (want {_REQUIRED_COLUMNS[c]})"
        for c in _REQUIRED_COLUMNS
        if c in actual and actual[c].lower() != _REQUIRED_COLUMNS[c]
    ]
    if missing or wrong_type:
        return {"ok": False, "details": {
            "missing": missing, "wrong_type": wrong_type, "actual": actual,
        }}
    return {"ok": True, "details": {"actual": actual}}


def _verify_fk_gone() -> dict[str, Any]:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'approval_tokens'::regclass AND contype = 'f'"
        )
        fks = cur.fetchall()
    bad = [name for name, _ in fks if "proposal_id" in name]
    if bad:
        return {"ok": False, "details": {
            "error": "FK on proposal_id still present", "constraints": bad,
        }}
    return {"ok": True, "details": {
        "foreign_keys_remaining": [name for name, _ in fks],
    }}


def _smoke_test() -> dict[str, Any]:
    """Mint a token + verify it via the full production code path. Token is
    consumed (used=true) so it cannot be reused; it leaves one disposable
    row in approval_tokens with a sentinel proposal_id for grep-ability."""
    # Force MODE=production for this in-process call regardless of how the
    # module-level constant was captured at import time.
    from nexus.overwatch_v2.auth import approval_tokens as at
    at.MODE = "production"
    sentinel_id = f"tool:phase15-smoke-{uuid.uuid4().hex[:8]}"
    payload = {"sentinel": "phase-1-5-1-pre-probe-smoke"}
    try:
        tok = at.issue_token(sentinel_id, payload, "phase-1-5-1@vaultscaler.com",
                             ttl_seconds=30)
        result = at.verify_token(tok, sentinel_id, payload)
    except Exception as exc:
        return {"ok": False, "details": {
            "error": f"{type(exc).__name__}: {exc}",
            "sentinel_proposal_id": sentinel_id,
        }}
    if not result.valid:
        return {"ok": False, "details": {
            "error": f"verify_token returned invalid: {result.reason}",
            "sentinel_proposal_id": sentinel_id,
        }}
    return {"ok": True, "details": {
        "sentinel_proposal_id": sentinel_id, "valid": True,
    }}


# ---- Orchestration --------------------------------------------------------

_STEP_ORDER = [
    "apply", "verify_schema_migrations", "verify_columns",
    "verify_fk_gone", "smoke_test",
]


def run(migration_path: str) -> dict[str, Any]:
    """Run apply + all 4 verifications. Halts on first failing step but
    emits a complete steps map (skipped steps marked ok=None)."""
    out: dict[str, Any] = {
        "ok": False,
        "applied_at_utc": None,
        "migration_filename": os.path.basename(migration_path),
        "checksum_sha256": None,
        "apply_status": None,
        "steps": {name: {"ok": None, "details": {}} for name in _STEP_ORDER},
        "failed_step": None,
    }

    # Step 1: apply
    try:
        apply_result = apply_migration_idempotent(migration_path)
    except (FileNotFoundError, ChecksumMismatchError) as e:
        out["steps"]["apply"] = {"ok": False, "details": {
            "error": f"{type(e).__name__}: {e}",
        }}
        out["failed_step"] = "apply"
        return out
    except Exception as e:  # network, auth, SQL syntax in migration body, etc
        out["steps"]["apply"] = {"ok": False, "details": {
            "error": f"{type(e).__name__}: {e}",
        }}
        out["failed_step"] = "apply"
        return out
    out["applied_at_utc"] = apply_result["applied_at_utc"]
    out["checksum_sha256"] = apply_result["checksum_sha256"]
    out["apply_status"] = apply_result["status"]
    out["steps"]["apply"] = {"ok": True, "details": apply_result}

    # Steps 2-5
    verifiers = [
        ("verify_schema_migrations",
         lambda: _verify_schema_migrations_row(out["migration_filename"])),
        ("verify_columns", _verify_approval_tokens_columns),
        ("verify_fk_gone", _verify_fk_gone),
        ("smoke_test", _smoke_test),
    ]
    for step_name, fn in verifiers:
        try:
            step_result = fn()
        except Exception as e:
            step_result = {"ok": False, "details": {
                "error": f"{type(e).__name__}: {e}",
            }}
        out["steps"][step_name] = step_result
        if not step_result["ok"]:
            out["failed_step"] = step_name
            return out

    out["ok"] = True
    return out


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(json.dumps({
            "ok": False,
            "failed_step": "args",
            "error": "usage: python -m nexus.operator.db_apply_migration_with_verify <migration-path>",
        }))
        return 2
    result = run(args[0])
    print(json.dumps(result, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Database connection helper for overwatch_v2 modules that need Postgres
outside the ontology layer (e.g. approval_tokens single-use ledger).

Pairs with the V2 RDS instance `overwatch-postgres` (provisioned by
infra/overwatch-v2/02-rds-postgres.yml).

Connection-config resolution order (Phase 1.5.1, 2026-04-26):
  1. `OVERWATCH_V2_DATABASE_URL` if set — preferred when an operator
     prefers a single pre-formatted URL (e.g. local development against
     a docker-compose Postgres, or a future single-secret deployment).
  2. Composed from `PG_HOST` + `PG_PORT` + `PG_USER` + `PG_PASSWORD` +
     `PG_DBNAME` if ALL FIVE are set — preferred for ECS task defs that
     use `secrets:` to unpack postgres-master's structured JSON keys
     (`host`, `port`, `username`, `password`, `dbname`) into individual
     env vars. Avoids a second pre-formatted-URL secret that would drift
     on master rotation; postgres-master remains single source of truth.
  3. `DBNotConfiguredError` — neither path satisfied.

Schema-prefix decision (Phase 1.5, 2026-04-26):
  Migrations create tables in the default `public` schema. The earlier
  `INSERT INTO overwatch_v2.approval_tokens` in approval_tokens.py was
  authorial accident — no migration creates a schema named `overwatch_v2`,
  no infra sets a search_path, and no other V2 module uses a schema prefix.
  Phase 1.5 drops the prefix from approval_tokens.py to match every other
  V2 caller. Do not re-add the prefix without first creating the schema in
  a migration AND setting search_path on the role.

Future consolidation candidate (NOT Phase 1.5 scope):
  Three private `_connect()` definitions exist today:
    - nexus/ontology/postgres.py            (V1, DATABASE_URL)
    - nexus/overwatch_v2/ontology/postgres.py  (V2, OVERWATCH_V2_DATABASE_URL)
    - nexus/aria_v2/persistence.py           (V2, OVERWATCH_V2_DATABASE_URL)
  The two V2 ones could collapse into this `get_conn`. Phase 1.5 deliberately
  does not refactor them — adding cross-module changes to a token-ledger
  PR widens blast radius beyond what the gap requires. Track separately.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

_PG_COMPONENTS = ("PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DBNAME")


class DBNotConfiguredError(RuntimeError):
    """No Postgres config — neither URL nor composable PG_* env vars."""


def _compose_url_from_pg_vars() -> str | None:
    """Return a composed postgres:// URL if all five PG_* vars are set, else None."""
    parts = {k: os.environ.get(k, "").strip() for k in _PG_COMPONENTS}
    if not all(parts.values()):
        return None
    user = quote_plus(parts["PG_USER"])
    password = quote_plus(parts["PG_PASSWORD"])
    return (
        f"postgres://{user}:{password}@"
        f"{parts['PG_HOST']}:{parts['PG_PORT']}/{parts['PG_DBNAME']}"
    )


def _get_database_url() -> str:
    url = os.environ.get("OVERWATCH_V2_DATABASE_URL", "").strip()
    if url:
        return url
    composed = _compose_url_from_pg_vars()
    if composed:
        return composed
    raise DBNotConfiguredError(
        "Neither OVERWATCH_V2_DATABASE_URL nor the full set "
        f"({', '.join(_PG_COMPONENTS)}) is set; "
        "see infra/overwatch-v2/02-rds-postgres.yml + the migration task def."
    )


@contextmanager
def get_conn() -> Iterator["object"]:
    """Yield a psycopg2 connection. Auto-commit on success, rollback on
    exception, close in finally. Mirrors `_connect` in postgres.py."""
    import psycopg2
    conn = psycopg2.connect(_get_database_url(), connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

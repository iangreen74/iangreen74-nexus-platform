"""Database connection helper for overwatch_v2 modules that need Postgres
outside the ontology layer (e.g. approval_tokens single-use ledger).

Pairs with the V2 RDS instance `overwatch-postgres` (provisioned by
infra/overwatch-v2/02-rds-postgres.yml). Uses the same env-var convention
as nexus/overwatch_v2/ontology/postgres.py:_connect — `OVERWATCH_V2_DATABASE_URL`
— so a single connection string covers both ontology writes and approval_tokens.

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

logger = logging.getLogger(__name__)


class DBNotConfiguredError(RuntimeError):
    """OVERWATCH_V2_DATABASE_URL not set — V2 RDS stack not deployed yet."""


def _get_database_url() -> str:
    url = os.environ.get("OVERWATCH_V2_DATABASE_URL", "").strip()
    if not url:
        raise DBNotConfiguredError(
            "OVERWATCH_V2_DATABASE_URL not set; "
            "see infra/overwatch-v2/02-rds-postgres.yml"
        )
    return url


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

"""V2 Postgres write layer for engineering_object_versions (migration 007).

Mirrors nexus/ontology/postgres.py shape but writes to the V2 RDS instance
(overwatch-postgres) and the engineering_object_versions table.

Driver: psycopg2 (matches existing repo's requirements.txt; spec drift
caught by P1 — the prompt said psycopg v3 but production uses v2).
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager

from nexus.overwatch_v2.ontology.exceptions import V2PostgresNotConfiguredError

logger = logging.getLogger(__name__)


def _get_database_url() -> str:
    url = os.environ.get("OVERWATCH_V2_DATABASE_URL", "").strip()
    if not url:
        raise V2PostgresNotConfiguredError(
            "OVERWATCH_V2_DATABASE_URL not set; "
            "see infra/overwatch-v2/02-rds-postgres.yml"
        )
    return url


@contextmanager
def _connect():
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


def insert_version(
    *,
    object_id: str,
    version_id: int,
    object_type: str,
    properties: dict,
    valid_from: str,
    created_by: str,
) -> None:
    """Insert a new version row. Caller computes version_id (no DB-side autoincrement)."""
    import psycopg2.extras
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO engineering_object_versions
                   (object_id, version_id, object_type, properties,
                    valid_from, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (object_id, version_id, object_type,
                 psycopg2.extras.Json(properties), valid_from, created_by),
            )
    logger.info("v2 version inserted: %s v=%s type=%s", object_id[:8], version_id, object_type)


def supersede_prior_version(object_id: str, valid_to: str) -> int:
    """Set valid_to on the current (valid_to IS NULL) row. Returns rows updated."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE engineering_object_versions
                   SET valid_to = %s
                   WHERE object_id = %s AND valid_to IS NULL""",
                (valid_to, object_id),
            )
            return cur.rowcount


def fetch_version(object_id: str, version: int | None = None) -> dict | None:
    """Return the matching version row, or current (valid_to IS NULL) if version is None."""
    with _connect() as conn:
        with conn.cursor() as cur:
            if version is None:
                cur.execute(
                    """SELECT object_id, version_id, object_type, properties,
                              created_at, valid_from, valid_to, created_by
                       FROM engineering_object_versions
                       WHERE object_id = %s AND valid_to IS NULL
                       LIMIT 1""",
                    (object_id,),
                )
            else:
                cur.execute(
                    """SELECT object_id, version_id, object_type, properties,
                              created_at, valid_from, valid_to, created_by
                       FROM engineering_object_versions
                       WHERE object_id = %s AND version_id = %s""",
                    (object_id, version),
                )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "version_id": row[1], "object_type": row[2],
        "properties": row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}"),
        "created_at": row[4].isoformat() if row[4] else None,
        "valid_from": row[5].isoformat() if row[5] else None,
        "valid_to": row[6].isoformat() if row[6] else None,
        "created_by": row[7],
    }

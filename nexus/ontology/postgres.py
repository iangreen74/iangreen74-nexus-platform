"""Postgres write layer for ontology versioning.

Sprint 13 Day 1 B5-prov. Pairs with infra/rds-ontology-postgres.yaml.
Raises PostgresNotConfiguredError if DATABASE_URL is unset — safe to
merge before the RDS stack is deployed.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


class PostgresNotConfiguredError(RuntimeError):
    """DATABASE_URL not set — B5-prov stack not deployed yet."""


def _get_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise PostgresNotConfiguredError(
            "DATABASE_URL not set — B5-prov stack not deployed yet; "
            "see infra/rds-ontology-postgres.yaml"
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


def write_version(
    *,
    ontology_id: str,
    tenant_id: str,
    project_id: str | None,
    object_type: str,
    object_data: dict[str, Any],
    proposed_via: str,
    superseded_by_version_id: str | None = None,
) -> str:
    """Insert a version row. Returns generated version_id."""
    import psycopg2.extras
    version_id = str(uuid.uuid4())
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ontology_object_versions
                   (version_id, ontology_id, tenant_id, project_id,
                    object_type, object_data, proposed_via,
                    superseded_by_version_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (version_id, ontology_id, tenant_id, project_id,
                 object_type.lower(), psycopg2.extras.Json(object_data),
                 proposed_via, superseded_by_version_id),
            )
    logger.info("ontology version written: %s for %s/%s",
                version_id[:8], tenant_id[:12], ontology_id[:8])
    return version_id

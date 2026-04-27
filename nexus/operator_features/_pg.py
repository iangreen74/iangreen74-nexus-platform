"""Internal Postgres connection helper shared by the report engine.

Both ``signal_evaluator.py`` and ``evidence_executor.py`` need to open
read-only connections to V1 (``DATABASE_URL``, ``nexus-ontology-postgres``)
or V2 (``OVERWATCH_V2_DATABASE_URL`` / ``PG_*``, ``overwatch-postgres``)
depending on which target the signal/evidence spec names.

Single shared helper keeps both modules under the 200-line CI cap and
prevents the routing logic from drifting between them.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def open_pg_connection(target: str):
    """Return an open psycopg2 connection context manager for ``target``.

    ``target='v2'`` delegates to ``nexus.overwatch_v2.db.get_conn``.
    ``target='v1'`` opens against ``DATABASE_URL`` env directly.
    Anything else raises ``ValueError``.
    """
    if target == "v2":
        from nexus.overwatch_v2.db import get_conn
        return get_conn()
    if target == "v1":
        return _v1_conn()
    raise ValueError(f"unknown postgres target: {target!r}")


@contextmanager
def _v1_conn() -> Iterator:
    import psycopg2  # noqa: WPS433
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL not set — V1 postgres requires it; "
            "the V1 task def or aria-console runtime should inject it "
            "from secret nexus/ontology/postgres/connection-XlBoLD"
        )
    conn = psycopg2.connect(url, connect_timeout=5)
    try:
        yield conn
    finally:
        conn.close()

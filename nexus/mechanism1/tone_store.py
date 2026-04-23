"""Tone marker persistence — writes and reads ToneMarkers via Postgres.

Storage: each ToneMarker becomes a row in tone_markers table with the
marker data as JSONB. Reads return last N markers for a tenant in
reverse chronological order (most recent first).

Uses the same _pg_connect() pattern as proposals.py.
"""
from __future__ import annotations

import json
import logging
import os

from nexus.mechanism1.tone import ToneMarker

log = logging.getLogger(__name__)


class ToneStoreNotConfiguredError(RuntimeError):
    """DATABASE_URL not set."""


def _pg_connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ToneStoreNotConfiguredError("DATABASE_URL not set")
    import psycopg2
    return psycopg2.connect(url, connect_timeout=5)


def save_marker(marker: ToneMarker) -> bool:
    """Insert one ToneMarker. Returns True on success, False on failure.

    Fire-and-forget — failures are logged, not raised.
    """
    try:
        conn = _pg_connect()
    except ToneStoreNotConfiguredError:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO tone_markers "
                    "(tenant_id, turn_id, detail, created_at) "
                    "VALUES (%s, %s, %s::jsonb, NOW())",
                    (marker.tenant_id, marker.turn_id,
                     json.dumps(marker.to_dict())),
                )
        return True
    except Exception as e:
        log.warning("save_marker failed: %s", e)
        return False
    finally:
        conn.close()


def read_markers(tenant_id: str, limit: int = 5) -> list[dict]:
    """Return the last N tone markers for a tenant, newest first.

    Returns empty list on any error — caller handles gracefully.
    """
    try:
        conn = _pg_connect()
    except ToneStoreNotConfiguredError:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT detail, created_at FROM tone_markers "
                    "WHERE tenant_id = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
        out = []
        for detail, created_at in rows:
            d = detail if isinstance(detail, dict) else json.loads(detail)
            d["created_at"] = created_at.isoformat() if created_at else None
            out.append(d)
        return out
    except Exception as e:
        log.warning("read_markers failed: %s", e)
        return []
    finally:
        conn.close()

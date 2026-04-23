"""Rolling summary persistence — Postgres read/write.

Same _pg_connect() pattern as tone_store.py and proposals.py.
Table: rolling_summaries (migration 005).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


class SummaryStoreNotConfiguredError(RuntimeError):
    """DATABASE_URL not set."""


def _pg_connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SummaryStoreNotConfiguredError("DATABASE_URL not set")
    import psycopg2
    return psycopg2.connect(url, connect_timeout=5)


def save_summary(
    tenant_id: str, horizon: str, text: str, for_date: date,
) -> bool:
    """Upsert one summary row. Returns True on success."""
    try:
        conn = _pg_connect()
    except SummaryStoreNotConfiguredError:
        return False
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO rolling_summaries "
                    "(tenant_id, horizon, for_date, text, created_at) "
                    "VALUES (%s, %s, %s, %s, NOW()) "
                    "ON CONFLICT (tenant_id, horizon, for_date) "
                    "DO UPDATE SET text = EXCLUDED.text, "
                    "created_at = NOW()",
                    (tenant_id, horizon, for_date, text),
                )
        return True
    except Exception as e:
        log.warning("save_summary failed: %s", e)
        return False
    finally:
        conn.close()


def read_summaries(tenant_id: str) -> dict[str, str | None]:
    """Return latest summary per horizon for a tenant.

    Returns {daily: str|None, weekly: str|None, monthly: str|None}.
    """
    result: dict[str, str | None] = {
        "daily": None, "weekly": None, "monthly": None,
    }
    try:
        conn = _pg_connect()
    except SummaryStoreNotConfiguredError:
        return result
    try:
        with conn:
            with conn.cursor() as cur:
                for horizon in ("daily", "weekly", "monthly"):
                    cur.execute(
                        "SELECT text FROM rolling_summaries "
                        "WHERE tenant_id = %s AND horizon = %s "
                        "ORDER BY for_date DESC LIMIT 1",
                        (tenant_id, horizon),
                    )
                    row = cur.fetchone()
                    if row:
                        result[horizon] = row[0]
        return result
    except Exception as e:
        log.warning("read_summaries failed: %s", e)
        return {"daily": None, "weekly": None, "monthly": None}
    finally:
        conn.close()


def read_past_digests(
    tenant_id: str, horizon: str, limit: int = 7,
) -> list[dict[str, Any]]:
    """Return last N summaries for a horizon, newest first.

    Used by weekly generator (reads daily digests) and monthly
    generator (reads weekly rollups).
    """
    try:
        conn = _pg_connect()
    except SummaryStoreNotConfiguredError:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT text, for_date, created_at "
                    "FROM rolling_summaries "
                    "WHERE tenant_id = %s AND horizon = %s "
                    "ORDER BY for_date DESC LIMIT %s",
                    (tenant_id, horizon, limit),
                )
                return [
                    {"text": r[0],
                     "for_date": r[1].isoformat() if r[1] else None,
                     "created_at": r[2].isoformat() if r[2] else None}
                    for r in cur.fetchall()
                ]
    except Exception as e:
        log.warning("read_past_digests failed: %s", e)
        return []
    finally:
        conn.close()

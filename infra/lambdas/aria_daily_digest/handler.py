"""Daily digest Lambda — runs at 2am UTC, generates daily summaries.

For each active tenant, calls generate_daily_digest() and saves
the result to rolling_summaries via save_summary().
"""
import logging
import os
import sys
from datetime import date, timedelta, timezone, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    yesterday = date.today() - timedelta(days=1)
    logger.info("Daily digest for %s", yesterday.isoformat())

    try:
        from nexus.summaries.generator import generate_daily_digest
        from nexus.summaries.store import save_summary
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        return {"statusCode": 200, "error": str(e)}

    tenant_ids = _active_tenants()
    generated = 0
    errors = 0
    for tid in tenant_ids:
        try:
            text = generate_daily_digest(tid)
            save_summary(tid, "daily", text, yesterday)
            generated += 1
            logger.info("Daily digest for %s: %d chars", tid[:12], len(text))
        except Exception as e:
            errors += 1
            logger.warning("Daily digest failed for %s: %s", tid[:12], e)

    return {
        "statusCode": 200,
        "date": yesterday.isoformat(),
        "generated": generated,
        "errors": errors,
    }


def _active_tenants() -> list[str]:
    """Return tenant_ids with recent activity (last 48h)."""
    try:
        import psycopg2
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return []
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT tenant_id FROM classifier_proposals "
                    "WHERE created_at > NOW() - INTERVAL '48 hours' "
                    "UNION "
                    "SELECT DISTINCT tenant_id FROM tone_markers "
                    "WHERE created_at > NOW() - INTERVAL '48 hours'"
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("_active_tenants failed: %s", e)
        return []

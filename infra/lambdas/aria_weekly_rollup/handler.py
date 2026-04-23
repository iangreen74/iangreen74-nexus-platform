"""Weekly rollup Lambda — runs Monday 2am UTC.

For each tenant with daily digests in the past week, generates a
weekly rollup and saves to rolling_summaries.
"""
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    last_sunday = date.today() - timedelta(days=1)
    logger.info("Weekly rollup for week ending %s", last_sunday.isoformat())

    try:
        from nexus.summaries.generator import generate_weekly_rollup
        from nexus.summaries.store import read_past_digests, save_summary
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        return {"statusCode": 200, "error": str(e)}

    tenant_ids = _tenants_with_dailies()
    generated = 0
    errors = 0
    for tid in tenant_ids:
        try:
            text = generate_weekly_rollup(tid)
            save_summary(tid, "weekly", text, last_sunday)
            generated += 1
            logger.info("Weekly rollup for %s: %d chars", tid[:12], len(text))
        except Exception as e:
            errors += 1
            logger.warning("Weekly rollup failed for %s: %s", tid[:12], e)

    return {
        "statusCode": 200,
        "week_ending": last_sunday.isoformat(),
        "generated": generated,
        "errors": errors,
    }


def _tenants_with_dailies() -> list[str]:
    """Tenants with daily digests in the past 7 days."""
    try:
        import psycopg2
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return []
        conn = psycopg2.connect(url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT DISTINCT tenant_id FROM rolling_summaries "
                    "WHERE horizon = 'daily' "
                    "AND for_date > CURRENT_DATE - 7"
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("_tenants_with_dailies failed: %s", e)
        return []

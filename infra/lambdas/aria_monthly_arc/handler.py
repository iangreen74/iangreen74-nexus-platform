"""Monthly arc Lambda — runs 1st of month 2am UTC.

For each tenant with weekly rollups in the past month, generates
a monthly arc and saves to rolling_summaries.
"""
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    last_month_end = date.today() - timedelta(days=1)
    logger.info("Monthly arc for month ending %s", last_month_end.isoformat())

    try:
        from nexus.summaries.generator import generate_monthly_arc
        from nexus.summaries.store import save_summary
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        return {"statusCode": 200, "error": str(e)}

    tenant_ids = _tenants_with_weeklies()
    generated = 0
    errors = 0
    for tid in tenant_ids:
        try:
            text = generate_monthly_arc(tid)
            save_summary(tid, "monthly", text, last_month_end)
            generated += 1
            logger.info("Monthly arc for %s: %d chars", tid[:12], len(text))
        except Exception as e:
            errors += 1
            logger.warning("Monthly arc failed for %s: %s", tid[:12], e)

    return {
        "statusCode": 200,
        "month_ending": last_month_end.isoformat(),
        "generated": generated,
        "errors": errors,
    }


def _tenants_with_weeklies() -> list[str]:
    """Tenants with weekly rollups in the past 35 days."""
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
                    "WHERE horizon = 'weekly' "
                    "AND for_date > CURRENT_DATE - 35"
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning("_tenants_with_weeklies failed: %s", e)
        return []

"""Mechanism 3 scheduler Lambda. Invoked hourly via EventBridge cron."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger()
log.setLevel(logging.INFO)


def lambda_handler(event, context):
    tenant_override = event.get("tenant_id_override")
    import psycopg2, boto3
    secret_arn = os.environ.get("DB_SECRET_ARN", "")
    if not secret_arn:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return {"status": "no_db_config"}
        conn = psycopg2.connect(db_url, connect_timeout=5)
    else:
        secrets = boto3.client("secretsmanager", region_name="us-east-1")
        secret = json.loads(
            secrets.get_secret_value(SecretId=secret_arn)["SecretString"])
        conn = psycopg2.connect(
            host=secret.get("host", ""), port=secret.get("port", 5432),
            dbname=secret.get("dbname", "ontology"),
            user=secret.get("username", ""), password=secret.get("password", ""),
            connect_timeout=5)
    try:
        tenants = [tenant_override] if tenant_override else _active_tenants(conn)
        log.info("scanning %d tenants", len(tenants))
        from nexus.mechanism3 import scan_tenant, save_prompts
        total = errors = 0
        for tid in tenants:
            try:
                prompts = scan_tenant(tid, db_conn=conn)
                total += save_prompts(prompts, conn)
            except Exception as e:
                errors += 1
                log.warning("scan %s failed: %s", tid, e)
        return {"status": "ok", "tenants": len(tenants),
                "prompts": total, "errors": errors}
    finally:
        conn.close()


def _active_tenants(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT tenant_id FROM (
                SELECT tenant_id FROM tone_markers
                WHERE created_at > NOW() - INTERVAL '48 hours'
                UNION
                SELECT tenant_id FROM classifier_proposals
                WHERE created_at > NOW() - INTERVAL '48 hours'
                UNION
                SELECT tenant_id FROM rolling_summaries
                WHERE created_at > NOW() - INTERVAL '48 hours'
            ) AS active
        """)
        return [r[0] for r in cur.fetchall()]

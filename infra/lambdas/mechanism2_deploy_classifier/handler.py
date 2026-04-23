"""Lambda handler for mechanism2-deploy-event-classifier.

Triggered by EventBridge rule on forgewing-deploy-events bus.
For each event: classify via Haiku, enqueue proposals to Postgres.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger()
log.setLevel(logging.INFO)


def lambda_handler(event, context):
    """Process a single EventBridge deploy event."""
    detail = event.get("detail", {})
    detail_type = event.get("detail-type", "")

    log.info("mechanism2: received %s tenant=%s",
             detail_type, detail.get("tenant_id", "?"))

    # Late imports so psycopg2 layer resolves at runtime
    from nexus.mechanism2.classifier import classify_deploy_event
    from nexus.mechanism2.store import enqueue_proposals

    try:
        proposals = classify_deploy_event(detail)
    except Exception as e:
        log.exception("mechanism2: classifier raised: %s", e)
        return {"status": "classifier_error", "proposals": 0}

    if not proposals:
        log.info("mechanism2: no proposals for event")
        return {"status": "no_proposals", "proposals": 0}

    import psycopg2
    import boto3

    secret_arn = os.environ.get("DB_SECRET_ARN", "")
    if not secret_arn:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            log.error("mechanism2: no DB_SECRET_ARN or DATABASE_URL")
            return {"status": "no_db_config", "proposals": 0}
        conn = psycopg2.connect(db_url, connect_timeout=5)
    else:
        secrets = boto3.client("secretsmanager", region_name="us-east-1")
        secret = json.loads(
            secrets.get_secret_value(SecretId=secret_arn)["SecretString"])
        conn = psycopg2.connect(
            host=secret.get("host", ""),
            port=secret.get("port", 5432),
            dbname=secret.get("dbname", "ontology"),
            user=secret.get("username", ""),
            password=secret.get("password", ""),
            connect_timeout=5,
        )

    try:
        count = enqueue_proposals(proposals, conn)
    finally:
        conn.close()

    log.info("mechanism2: enqueued %d proposals", count)
    return {"status": "ok", "proposals": count}

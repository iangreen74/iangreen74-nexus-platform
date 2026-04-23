"""Lambda handler for AskCustomer enqueue.

Invoked by Step Functions with .waitForTaskToken pattern. Receives
tenant_id, project_id, question, options, and task_token. Creates a
pending ask in Postgres and returns the proposal_id.

Requires: DATABASE_URL env var, psycopg2 Lambda layer, VPC config
to reach RDS.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """Lambda entry point for AskCustomer enqueue."""
    try:
        tenant_id = event.get("tenant_id", "")
        project_id = event.get("project_id", "")
        question = event.get("question", "")
        options = event.get("options", [])
        task_token = event.get("task_token", "")
        ctx = event.get("context", {})

        if not tenant_id or not question:
            return _response(400, {"error": "tenant_id and question required"})

        import psycopg2
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return _response(500, {"error": "DATABASE_URL not configured"})

        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        conn = psycopg2.connect(db_url, connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ask_customer_state "
                    "(proposal_id, tenant_id, project_id, question, "
                    "options, task_token, context, status, created_at) "
                    "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, "
                    "'pending', %s)",
                    (proposal_id, tenant_id, project_id, question,
                     json.dumps(options), task_token or None,
                     json.dumps(ctx), now.isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

        logger.info("askcustomer-lambda: enqueued %s for %s",
                    proposal_id[:8], tenant_id[:12])

        return _response(200, {
            "proposal_id": proposal_id,
            "tenant_id": tenant_id,
            "status": "pending",
        })

    except Exception as e:
        logger.exception("askcustomer-lambda: enqueue failed")
        return _response(500, {"error": str(e)[:300]})


def _response(code: int, body: dict) -> dict:
    return {
        "statusCode": code,
        "body": json.dumps(body),
        "headers": {"Content-Type": "application/json"},
    }

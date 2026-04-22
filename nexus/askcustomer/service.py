"""AskCustomer service layer.

enqueue_ask() — create a pending proposal, optionally store SFN task token
resolve_ask() — mark answered, signal SFN to resume if task_token present
list_pending() — list pending asks for a tenant

Writes to Postgres (source of truth). ActionEvents written to eval corpus
on both enqueue and resolve.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AskCustomerNotConfiguredError(RuntimeError):
    """DATABASE_URL not set — Postgres not available."""


def _pg_connect():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise AskCustomerNotConfiguredError(
            "DATABASE_URL not set — AskCustomer requires Postgres"
        )
    import psycopg2
    return psycopg2.connect(url, connect_timeout=5)


def _sfn_client():
    try:
        import boto3
        return boto3.client("stepfunctions", region_name="us-east-1")
    except Exception:
        return None


def _write_eval_event(*, mutation_kind: str, tenant_id: str,
                       project_id: str | None, proposal_id: str,
                       old_state: dict | None, new_state: dict) -> None:
    try:
        from nexus.ontology.eval_corpus import write_action_event
        write_action_event(
            tenant_id=tenant_id, project_id=project_id,
            ontology_id=proposal_id, version_id=proposal_id,
            object_type="askcustomer", mutation_kind=mutation_kind,
            caller="system", proposed_via="investigation",
            old_state=old_state, new_state=new_state,
        )
    except Exception as e:
        logger.warning("eval_corpus write failed: %s", e)


def enqueue_ask(
    *,
    tenant_id: str,
    project_id: str | None,
    question: str,
    options: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    task_token: str | None = None,
    execution_arn: str | None = None,
    expires_at: datetime | None = None,
) -> str:
    """Create a pending proposal. Returns proposal_id."""
    proposal_id = str(uuid.uuid4())
    conn = _pg_connect()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ask_customer_state "
                    "(proposal_id, tenant_id, project_id, question, options, "
                    "context, task_token, state_machine_execution_arn, "
                    "status, created_at, expires_at) "
                    "VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,'pending',NOW(),%s)",
                    (proposal_id, tenant_id, project_id, question,
                     json.dumps(options), json.dumps(context or {}),
                     task_token, execution_arn, expires_at),
                )
    finally:
        conn.close()

    _write_eval_event(
        mutation_kind="enqueue", tenant_id=tenant_id,
        project_id=project_id, proposal_id=proposal_id,
        old_state=None,
        new_state={"question": question, "options": options},
    )
    logger.info("askcustomer: enqueued %s for %s", proposal_id[:8], tenant_id[:12])
    return proposal_id


def resolve_ask(
    *,
    proposal_id: str,
    answer: dict[str, Any],
    answered_by: str,
) -> dict[str, Any]:
    """Mark answered + signal SFN if task_token present."""
    conn = _pg_connect()
    task_token = None
    tenant_id = project_id = question = ""
    options: list = []
    context: dict = {}
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tenant_id, project_id, question, options, "
                    "context, task_token, status "
                    "FROM ask_customer_state WHERE proposal_id = %s FOR UPDATE",
                    (proposal_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise ValueError(f"Proposal {proposal_id} not found")
                tenant_id, project_id, question, options, context, task_token, status = row
                if status != "pending":
                    raise ValueError(f"Proposal {proposal_id} is {status}")
                cur.execute(
                    "UPDATE ask_customer_state SET answer = %s::jsonb, "
                    "answered_by = %s, status = 'answered', answered_at = NOW() "
                    "WHERE proposal_id = %s",
                    (json.dumps(answer), answered_by, proposal_id),
                )
    finally:
        conn.close()

    if task_token:
        sfn = _sfn_client()
        if sfn:
            try:
                sfn.send_task_success(
                    taskToken=task_token,
                    output=json.dumps({"answer": answer, "proposal_id": proposal_id}),
                )
            except Exception as e:
                logger.error("SFN send_task_success failed: %s", e)

    _write_eval_event(
        mutation_kind="resolve", tenant_id=tenant_id,
        project_id=project_id, proposal_id=proposal_id,
        old_state={"question": question, "options": options},
        new_state={"answer": answer},
    )
    logger.info("askcustomer: resolved %s by %s", proposal_id[:8], answered_by)
    return {"proposal_id": proposal_id, "tenant_id": tenant_id,
            "project_id": project_id, "answer": answer, "status": "answered"}


def list_pending(tenant_id: str, project_id: str | None = None) -> list[dict[str, Any]]:
    """List pending asks for a tenant."""
    try:
        conn = _pg_connect()
    except AskCustomerNotConfiguredError:
        return []
    try:
        with conn:
            with conn.cursor() as cur:
                if project_id:
                    cur.execute(
                        "SELECT proposal_id, question, options, context, "
                        "created_at, expires_at FROM ask_customer_state "
                        "WHERE tenant_id = %s AND project_id = %s AND status = 'pending' "
                        "ORDER BY created_at DESC",
                        (tenant_id, project_id),
                    )
                else:
                    cur.execute(
                        "SELECT proposal_id, question, options, context, "
                        "created_at, expires_at FROM ask_customer_state "
                        "WHERE tenant_id = %s AND status = 'pending' "
                        "ORDER BY created_at DESC",
                        (tenant_id,),
                    )
                return [
                    {"proposal_id": str(r[0]), "question": r[1], "options": r[2],
                     "context": r[3],
                     "created_at": r[4].isoformat() if r[4] else None,
                     "expires_at": r[5].isoformat() if r[5] else None}
                    for r in cur.fetchall()
                ]
    finally:
        conn.close()

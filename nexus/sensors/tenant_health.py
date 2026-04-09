"""
Tenant Health Sensor.

READ ONLY. Never mutates Forgewing infrastructure or graph state.
Aggregates three dimensions of tenant health:

    deployment  — CF stack + ECS service + ALB HTTP reachability
    pipeline    — stuck tasks, PR cadence, task freshness
    conversation— last message recency, message count

and collapses them into an overall_status: healthy / degraded / critical.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from nexus import aws_client, neptune_client
from nexus.config import (
    FORGEWING_WEB,
    HEALTH_CHECK_TIMEOUT_SECONDS,
    MODE,
    TENANT_INACTIVE_HOURS,
)

logger = logging.getLogger("nexus.sensors.tenant")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _check_app_reachable(tenant_id: str) -> dict[str, Any]:
    """HTTP health check against the tenant's deployed app."""
    url = f"{FORGEWING_WEB}/tenants/{tenant_id}/health"
    if MODE != "production":
        return {"url": url, "reachable": True, "status_code": 200}
    try:
        resp = httpx.get(url, timeout=HEALTH_CHECK_TIMEOUT_SECONDS)
        return {
            "url": url,
            "reachable": resp.status_code < 500,
            "status_code": resp.status_code,
        }
    except Exception as exc:
        logger.warning("HTTP check failed for %s: %s", url, exc)
        return {"url": url, "reachable": False, "status_code": None}


def _pipeline_snapshot(tenant_id: str) -> dict[str, Any]:
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=25)
    prs = neptune_client.get_recent_prs(tenant_id, limit=10)
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    stuck = []
    now = _now()
    for task in in_progress:
        created = _parse_ts(task.get("created_at"))
        if created and now - created > timedelta(hours=6):
            stuck.append(task.get("id"))
    last_pr_ts = _parse_ts((prs[0] if prs else {}).get("created_at"))
    return {
        "last_pr_at": last_pr_ts.isoformat() if last_pr_ts else None,
        "tasks_in_progress": len(in_progress),
        "stuck_task_count": len(stuck),
        "stuck_task_ids": stuck,
        "total_recent_tasks": len(tasks),
    }


def _conversation_snapshot(tenant_id: str) -> dict[str, Any]:
    count = neptune_client.get_conversation_count(tenant_id)
    # Last-message freshness comes from most recent task proxy in local mode.
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=1)
    last_ts = _parse_ts((tasks[0] if tasks else {}).get("created_at"))
    inactive = False
    if last_ts:
        inactive = _now() - last_ts > timedelta(hours=TENANT_INACTIVE_HOURS)
    return {
        "message_count": count,
        "last_message_at": last_ts.isoformat() if last_ts else None,
        "inactive": inactive,
    }


def _rollup(deployment: dict, pipeline: dict, conversation: dict) -> str:
    if not deployment.get("healthy"):
        return "critical"
    if pipeline.get("stuck_task_count", 0) > 2:
        return "critical"
    if conversation.get("inactive") or pipeline.get("stuck_task_count", 0) > 0:
        return "degraded"
    return "healthy"


def check_tenant(tenant_id: str) -> dict[str, Any]:
    """Build a full TenantHealthReport for one tenant. Never raises."""
    try:
        infra = aws_client.describe_tenant_infra(tenant_id)
        reachability = _check_app_reachable(tenant_id)
        deployment = {
            "stack": infra.get("stack", {}),
            "services": infra.get("services", []),
            "reachable": reachability.get("reachable"),
            "healthy": bool(infra.get("healthy") and reachability.get("reachable")),
        }
        pipeline = _pipeline_snapshot(tenant_id)
        conversation = _conversation_snapshot(tenant_id)
        return {
            "tenant_id": tenant_id,
            "context": neptune_client.get_tenant_context(tenant_id),
            "deployment": deployment,
            "pipeline": pipeline,
            "conversation": conversation,
            "overall_status": _rollup(deployment, pipeline, conversation),
            "checked_at": _now().isoformat(),
        }
    except Exception:
        logger.exception("check_tenant(%s) crashed", tenant_id)
        return {
            "tenant_id": tenant_id,
            "overall_status": "critical",
            "error": True,
            "checked_at": _now().isoformat(),
        }


def check_all_tenants() -> list[dict[str, Any]]:
    """Run check_tenant for every tenant known to Neptune."""
    ids = neptune_client.get_tenant_ids()
    return [check_tenant(tid) for tid in ids]

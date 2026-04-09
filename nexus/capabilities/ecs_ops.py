"""
ECS Operation Capabilities.

Restart an ECS service by forcing a new deployment, and pull recent
log entries for diagnosis. Both are registered with the global
CapabilityRegistry so they're subject to rate limits and logging.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.aws_client import _client
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_MODERATE, BLAST_SAFE, MODE

logger = logging.getLogger("nexus.capabilities.ecs")


def restart_service(cluster: str, service: str) -> dict[str, Any]:
    """Force a new deployment for an ECS service."""
    if MODE != "production":
        logger.info("[local] would restart %s/%s", cluster, service)
        return {"cluster": cluster, "service": service, "restarted": True, "mock": True}
    try:
        resp = _client("ecs").update_service(
            cluster=cluster, service=service, forceNewDeployment=True
        )
        deployment = (resp.get("service", {}).get("deployments") or [{}])[0]
        return {
            "cluster": cluster,
            "service": service,
            "restarted": True,
            "deployment_id": deployment.get("id"),
            "status": deployment.get("status"),
        }
    except Exception as exc:
        logger.exception("restart_service(%s/%s) failed", cluster, service)
        return {"cluster": cluster, "service": service, "restarted": False, "error": str(exc)}


def get_service_logs(cluster: str, service: str, minutes: int = 30) -> list[dict[str, Any]]:
    """Fetch recent log events for an ECS service."""
    log_group = f"/ecs/{service}"
    if MODE != "production":
        return [{"timestamp": datetime.now(timezone.utc).isoformat(), "message": "[mock] no logs"}]
    try:
        end = int(datetime.now(timezone.utc).timestamp() * 1000)
        start = end - minutes * 60 * 1000
        resp = _client("logs").filter_log_events(
            logGroupName=log_group, startTime=start, endTime=end, limit=100
        )
        return [
            {"timestamp": ev.get("timestamp"), "message": ev.get("message", "")}
            for ev in resp.get("events", [])
        ]
    except Exception:
        logger.exception("get_service_logs(%s/%s) failed", cluster, service)
        return []


registry.register(
    Capability(
        name="restart_service",
        function=restart_service,
        blast_radius=BLAST_MODERATE,
        description="Force a new deployment for an ECS service (reversible).",
        requires_approval=False,
    )
)

registry.register(
    Capability(
        name="get_service_logs",
        function=get_service_logs,
        blast_radius=BLAST_SAFE,
        description="Fetch recent CloudWatch log events for an ECS service.",
    )
)

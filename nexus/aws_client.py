"""
AWS Client Wrapper — ECS, CloudWatch, Secrets Manager, CloudFormation.

Everything NEXUS needs from AWS flows through this module so we have
one place to mock in local mode and one place to swap SDK clients
or add retry/backoff. No other module should import boto3 directly.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import AWS_REGION, FORGEWING_CLUSTER, MODE

logger = logging.getLogger("nexus.aws")

# Lazy boto3 clients
_clients: dict[str, Any] = {}


def _client(service: str):
    """Return a boto3 client, caching one per service. Production only."""
    if service not in _clients:
        import boto3  # noqa: WPS433 — lazy import keeps local mode dep-free

        _clients[service] = boto3.client(service, region_name=AWS_REGION)
    return _clients[service]


def get_ecs_service_status(cluster: str, service: str) -> dict[str, Any]:
    """Return running/desired count and deployment health for an ECS service."""
    if MODE != "production":
        return {
            "service": service,
            "cluster": cluster,
            "running_count": 1,
            "desired_count": 1,
            "pending_count": 0,
            "status": "ACTIVE",
            "healthy": True,
        }
    try:
        resp = _client("ecs").describe_services(cluster=cluster, services=[service])
        svc = (resp.get("services") or [{}])[0]
        running = svc.get("runningCount", 0)
        desired = svc.get("desiredCount", 0)
        return {
            "service": service,
            "cluster": cluster,
            "running_count": running,
            "desired_count": desired,
            "pending_count": svc.get("pendingCount", 0),
            "status": svc.get("status", "UNKNOWN"),
            "healthy": running == desired and desired > 0,
        }
    except Exception:
        logger.exception("get_ecs_service_status(%s/%s) failed", cluster, service)
        return {"service": service, "cluster": cluster, "healthy": False, "error": True}


def get_ecs_services(cluster: str = FORGEWING_CLUSTER) -> list[dict[str, Any]]:
    """Return status dicts for every service in a cluster."""
    if MODE != "production":
        from nexus.config import FORGEWING_SERVICES

        return [get_ecs_service_status(cluster, s) for s in FORGEWING_SERVICES]
    try:
        arns = _client("ecs").list_services(cluster=cluster).get("serviceArns", [])
        names = [a.split("/")[-1] for a in arns]
        return [get_ecs_service_status(cluster, n) for n in names]
    except Exception:
        logger.exception("get_ecs_services(%s) failed", cluster)
        return []


def get_cloudwatch_errors(log_group: str, minutes: int = 30) -> int:
    """Count ERROR-level log events in the last `minutes`."""
    if MODE != "production":
        return 0
    try:
        end = int(datetime.now(timezone.utc).timestamp() * 1000)
        start = end - minutes * 60 * 1000
        resp = _client("logs").filter_log_events(
            logGroupName=log_group,
            startTime=start,
            endTime=end,
            filterPattern="ERROR",
            limit=1000,
        )
        return len(resp.get("events", []))
    except Exception:
        logger.exception("get_cloudwatch_errors(%s) failed", log_group)
        return 0


def get_secret(secret_id: str) -> dict[str, Any]:
    """
    Fetch a secret from Secrets Manager.

    Returns a dict in all cases. JSON secrets are parsed; plain-string
    secrets (e.g. a raw GitHub PAT) come back as `{"_raw": "..."}` so
    callers can probe both shapes.
    """
    if MODE != "production":
        return {"mock": True, "secret_id": secret_id, "_raw": "mock-secret"}
    try:
        resp = _client("secretsmanager").get_secret_value(SecretId=secret_id)
        raw = resp.get("SecretString", "")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parsed.setdefault("_raw", raw)
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return {"_raw": raw}
    except Exception:
        logger.exception("get_secret(%s) failed", secret_id)
        return {}


def get_cf_stack_status(stack_name: str) -> dict[str, Any]:
    """Return the current CloudFormation stack status."""
    if MODE != "production":
        return {
            "stack_name": stack_name,
            "status": "CREATE_COMPLETE",
            "healthy": True,
        }
    try:
        resp = _client("cloudformation").describe_stacks(StackName=stack_name)
        stack = (resp.get("Stacks") or [{}])[0]
        status = stack.get("StackStatus", "UNKNOWN")
        return {
            "stack_name": stack_name,
            "status": status,
            "healthy": status.endswith("_COMPLETE") and "ROLLBACK" not in status,
            "updated_at": str(stack.get("LastUpdatedTime") or stack.get("CreationTime")),
        }
    except Exception:
        logger.exception("get_cf_stack_status(%s) failed", stack_name)
        return {"stack_name": stack_name, "healthy": False, "error": True}


def describe_tenant_infra(tenant_id: str) -> dict[str, Any]:
    """Summarize a tenant's CF stack and ECS posture."""
    stack_name = f"forgewing-{tenant_id}"
    stack = get_cf_stack_status(stack_name)
    services = (
        get_ecs_services(f"forgewing-{tenant_id}")
        if MODE == "production"
        else [{"service": f"{tenant_id}-app", "healthy": True, "running_count": 1, "desired_count": 1}]
    )
    return {
        "tenant_id": tenant_id,
        "stack": stack,
        "services": services,
        "healthy": stack.get("healthy", False) and all(s.get("healthy") for s in services),
    }

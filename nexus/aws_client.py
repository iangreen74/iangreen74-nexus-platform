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


# --- Tenant CF stack discovery -------------------------------------------------
#
# Real ForgeScaler tenant stacks follow the convention:
#     ForgeScaler-{tenant_id_short}     (the customer's main stack)
#     forgescaler-deploy-{tenant_id_short}   (the deployment stack)
#
# where `tenant_id_short` is the prefix of the tenant_id (typically the first
# 13-14 chars: `forge-` plus 7 hex chars). The exact truncation isn't fixed, so
# we list every active ForgeScaler stack once, then prefix-match each tenant.
_FORGESCALER_TENANT_STACK_PREFIXES = ("ForgeScaler-", "forgescaler-deploy-")
_HEALTHY_STATUSES = (
    "CREATE_COMPLETE",
    "UPDATE_COMPLETE",
    "UPDATE_ROLLBACK_COMPLETE",
)
_tenant_stack_cache: dict[str, list[dict[str, Any]]] | None = None


def _list_tenant_stacks() -> list[dict[str, Any]]:
    """
    Return all active ForgeScaler customer/deployment stacks.

    Result is cached for the process lifetime since stack names rarely change
    and listing every call would burn API budget. Each entry is
    `{name, status, type}` where type is `customer` or `deploy`.
    """
    global _tenant_stack_cache
    if _tenant_stack_cache is not None:
        return _tenant_stack_cache.get("stacks", [])
    if MODE != "production":
        _tenant_stack_cache = {"stacks": []}
        return []
    stacks: list[dict[str, Any]] = []
    try:
        paginator = _client("cloudformation").get_paginator("list_stacks")
        for page in paginator.paginate(StackStatusFilter=list(_HEALTHY_STATUSES)):
            for s in page.get("StackSummaries", []):
                name = s.get("StackName", "")
                if name.startswith("ForgeScaler-"):
                    stacks.append({"name": name, "status": s.get("StackStatus"), "type": "customer"})
                elif name.startswith("forgescaler-deploy-"):
                    stacks.append({"name": name, "status": s.get("StackStatus"), "type": "deploy"})
    except Exception:
        logger.exception("_list_tenant_stacks failed")
    _tenant_stack_cache = {"stacks": stacks}
    return stacks


def reset_tenant_stack_cache() -> None:
    """Drop the tenant stack cache so the next call re-lists from CloudFormation."""
    global _tenant_stack_cache
    _tenant_stack_cache = None


def _find_stack_for_tenant(tenant_id: str) -> dict[str, Any] | None:
    """Find the ForgeScaler-* customer stack belonging to a tenant."""
    if not tenant_id:
        return None
    all_stacks = _list_tenant_stacks()
    if not all_stacks:
        return None
    # Try shortest viable prefix first: many tenant IDs are truncated when
    # used as stack suffixes (e.g. forge-1dba4143ca24ed1f -> forge-1dba414).
    candidates: list[dict[str, Any]] = []
    for stack in all_stacks:
        if stack["type"] != "customer":
            continue
        suffix = stack["name"].removeprefix("ForgeScaler-")
        if tenant_id.startswith(suffix) or suffix.startswith(tenant_id):
            candidates.append(stack)
    if not candidates:
        return None
    # Prefer the longest-matching suffix (most specific).
    candidates.sort(key=lambda s: len(s["name"]), reverse=True)
    return candidates[0]


def describe_tenant_infra(tenant_id: str) -> dict[str, Any]:
    """
    Summarize a tenant's deployment infrastructure using ground truth.

    Priority 1: Check the actual app URL (live HTTP check)
    Priority 2: Check DeploymentProgress / DeployedService / DeploymentStack in Neptune
    Priority 3: Report as not provisioned

    This gives the REAL deploy state, not stale Neptune data.
    """
    if MODE != "production":
        return {
            "tenant_id": tenant_id,
            "stack": {"stack_name": f"ForgeScaler-{tenant_id}", "status": "CREATE_COMPLETE", "healthy": True},
            "services": [{"service": f"{tenant_id}-app", "healthy": True, "running_count": 1, "desired_count": 1}],
            "healthy": True,
            "provisioned": True,
        }

    from nexus.sensors.ground_truth import get_deploy_ground_truth

    gt = get_deploy_ground_truth(tenant_id)
    deploy_status = gt.get("deploy_status", "unknown")

    if deploy_status == "live":
        return {
            "tenant_id": tenant_id,
            "stack": {"app_url": gt.get("app_url"), "status": "live", "http_status": gt.get("http_status")},
            "services": [{"url": gt.get("app_url"), "healthy": True}],
            "healthy": True,
            "provisioned": True,
        }
    elif deploy_status == "deploying":
        return {
            "tenant_id": tenant_id,
            "stack": {"stage": gt.get("stage"), "status": "deploying"},
            "services": [],
            "healthy": False,
            "provisioned": True,
        }
    elif deploy_status == "not_started":
        return {
            "tenant_id": tenant_id,
            "stack": None,
            "services": [],
            "healthy": False,
            "provisioned": False,
            "reason": "no deploy progress or app URL found",
        }
    else:
        return {
            "tenant_id": tenant_id,
            "stack": {"app_url": gt.get("app_url"), "status": deploy_status, "http_status": gt.get("http_status")},
            "services": [],
            "healthy": False,
            "provisioned": gt.get("app_url") is not None,
            "reason": f"deploy status: {deploy_status}",
        }

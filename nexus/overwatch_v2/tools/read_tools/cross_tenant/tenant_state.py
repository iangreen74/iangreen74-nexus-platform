"""Tool 8 — read_customer_tenant_state: ECS + ALB + recent deploys per tenant.

Path γ: same-account multi-tenant. Filters by naming pattern
`forgescaler-forge-{short}-*`. Asserts tenant scope on every result.
Audits to /overwatch-v2/cross-tenant-audit.
"""
from __future__ import annotations

from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)
from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    _audit_cross_tenant_call, _assert_tenant_scoped, _validate_tenant_id,
    _expected_resource_prefix,
)


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {"type": "string", "description": "Forgewing tenant ID, e.g. forge-1dba4143ca24ed1f"},
    },
    "required": ["tenant_id"],
}


def _client(service: str):
    from nexus.aws_client import _client as factory
    return factory(service)


def _read_ecs_services(short: str, expected_prefix: str) -> list[dict[str, Any]]:
    cluster_name = f"forgescaler-forge-{short}-cluster"
    ecs = _client("ecs")
    try:
        arns = ecs.list_services(cluster=cluster_name).get("serviceArns") or []
    except ecs.exceptions.ClusterNotFoundException:
        return []
    if not arns:
        return []
    descs = ecs.describe_services(cluster=cluster_name, services=arns).get("services", [])
    out: list[dict[str, Any]] = []
    for svc in descs:
        deployments = [
            {"status": d.get("status"),
             "rollout_state": d.get("rolloutState"),
             "running": d.get("runningCount"),
             "desired": d.get("desiredCount"),
             "task_definition": d.get("taskDefinition", "").split("/")[-1]}
            for d in svc.get("deployments", [])
        ]
        out.append({
            "name": svc.get("serviceName"),
            "desired": svc.get("desiredCount"),
            "running": svc.get("runningCount"),
            "deployments": deployments,
        })
    _assert_tenant_scoped(out, f"forge-{short}", "name")
    return out


def _read_alb_targets(short: str) -> list[dict[str, Any]]:
    elbv2 = _client("elbv2")
    tg_name = f"forgescaler-forge-{short}-tg"
    try:
        tgs = elbv2.describe_target_groups(Names=[tg_name]).get("TargetGroups", [])
    except elbv2.exceptions.TargetGroupNotFoundException:
        return []
    out: list[dict[str, Any]] = []
    for tg in tgs:
        health = elbv2.describe_target_health(
            TargetGroupArn=tg["TargetGroupArn"],
        ).get("TargetHealthDescriptions", [])
        healthy = sum(1 for h in health if h.get("TargetHealth", {}).get("State") == "healthy")
        unhealthy = sum(1 for h in health if h.get("TargetHealth", {}).get("State") != "healthy")
        out.append({
            "name": tg["TargetGroupName"],
            "healthy_count": healthy,
            "unhealthy_count": unhealthy,
            "total_targets": len(health),
        })
    _assert_tenant_scoped(out, f"forge-{short}", "name")
    return out


def _read_recent_deploys(short: str) -> list[dict[str, Any]]:
    cfn = _client("cloudformation")
    stack_prefix = f"forgescaler-forge-{short}-"
    try:
        stacks = cfn.list_stacks(
            StackStatusFilter=[
                "CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_IN_PROGRESS",
                "CREATE_IN_PROGRESS", "ROLLBACK_COMPLETE", "UPDATE_ROLLBACK_COMPLETE",
            ],
        ).get("StackSummaries", [])
    except Exception as e:
        raise map_boto_error(e) from e
    matched = [s for s in stacks if (s.get("StackName") or "").startswith(stack_prefix)]
    out: list[dict[str, Any]] = []
    for s in matched[:10]:
        out.append({
            "name": s["StackName"],
            "status": s.get("StackStatus"),
            "last_updated": (s.get("LastUpdatedTime") or s.get("CreationTime")).isoformat()
                if s.get("LastUpdatedTime") or s.get("CreationTime") else None,
        })
    _assert_tenant_scoped(out, f"forge-{short}", "name")
    return out


def handler(**params: Any) -> dict[str, Any]:
    tenant_id = params.get("tenant_id", "")
    try:
        short = _validate_tenant_id(tenant_id)
    except ValueError as e:
        raise ToolUnknown(str(e)) from e
    expected = _expected_resource_prefix(tenant_id)
    resources_read: list[str] = []
    try:
        ecs_services = _read_ecs_services(short, expected)
        alb_targets = _read_alb_targets(short)
        recent_deploys = _read_recent_deploys(short)
    except Exception as e:
        _audit_cross_tenant_call(
            tenant_id, "read_customer_tenant_state",
            resources_read, 0, error=str(e),
        )
        if isinstance(e, (ToolUnknown, AssertionError)):
            raise
        raise map_boto_error(e) from e
    resources_read = [s["name"] for s in ecs_services] + \
                     [t["name"] for t in alb_targets] + \
                     [d["name"] for d in recent_deploys]
    result_count = len(ecs_services) + len(alb_targets) + len(recent_deploys)
    _audit_cross_tenant_call(
        tenant_id, "read_customer_tenant_state", resources_read, result_count,
    )
    from datetime import datetime, timezone
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "ecs_services": ecs_services,
        "alb_targets": alb_targets,
        "recent_deploys": recent_deploys,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_customer_tenant_state",
        description=(
            "Phase 0c cross-tenant read. Returns ECS services, ALB target health, "
            "and recent CFN deploys for a Forgewing tenant. Filters by naming "
            "pattern; asserts tenant scope on results; audits each call."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

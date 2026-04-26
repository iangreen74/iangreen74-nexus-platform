"""Phase 1 read_customer_tenant_state — operational state of a tenant.

Returns ECS service status, ALB target health, and recent deployment
events for the given tenant. Tenant-scoped via TenantId tag (path γ
+ three guardrails — see _tenant_scope.py).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools._tenant_scope import (
    assert_resource_belongs,
    list_tenant_resources,
    require_tenant_id,
    write_audit_event,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_customer_tenant_state"

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {
            "type": "string",
            "description": "Full tenant ID (e.g. forge-1dba4143ca24ed1f).",
        },
    },
    "required": ["tenant_id"],
}


def _ecs_state(client, cluster_arn: str, service_arns: list[str]) -> list[dict]:
    if not service_arns:
        return []
    out: list[dict] = []
    for chunk_start in range(0, len(service_arns), 10):
        chunk = service_arns[chunk_start:chunk_start + 10]
        resp = client.describe_services(cluster=cluster_arn, services=chunk)
        for svc in resp.get("services", []) or []:
            out.append({
                "name": svc.get("serviceName"),
                "status": svc.get("status"),
                "desired": svc.get("desiredCount", 0),
                "running": svc.get("runningCount", 0),
                "pending": svc.get("pendingCount", 0),
                "task_definition": svc.get("taskDefinition"),
                "rollout_state": (
                    (svc.get("deployments") or [{}])[0].get("rolloutState")
                ),
            })
    return out


def _tg_health(client, target_group_arns: list[str]) -> list[dict]:
    out: list[dict] = []
    for arn in target_group_arns:
        try:
            resp = client.describe_target_health(TargetGroupArn=arn)
        except Exception:
            continue
        states = [
            (d.get("TargetHealth") or {}).get("State", "?")
            for d in resp.get("TargetHealthDescriptions", []) or []
        ]
        healthy = sum(1 for s in states if s == "healthy")
        unhealthy = sum(1 for s in states if s and s != "healthy")
        out.append({
            "target_group_arn": arn,
            "tg_name": arn.rsplit("/", 2)[1] if "/" in arn else arn,
            "healthy_count": healthy,
            "unhealthy_count": unhealthy,
            "states": states,
        })
    return out


def _recent_cfn_events(client, stack_name: str, limit: int = 10) -> list[dict]:
    try:
        resp = client.describe_stack_events(StackName=stack_name)
    except Exception:
        return []
    out: list[dict] = []
    for ev in (resp.get("StackEvents") or [])[:limit]:
        out.append({
            "timestamp": str(ev.get("Timestamp")),
            "logical_id": ev.get("LogicalResourceId"),
            "resource_type": ev.get("ResourceType"),
            "status": ev.get("ResourceStatus"),
            "reason": (ev.get("ResourceStatusReason") or "")[:300],
        })
    return out


def handler(**params: Any) -> dict:
    tenant_id = require_tenant_id(params.get("tenant_id"))
    from nexus.aws_client import _client
    try:
        resources = list_tenant_resources(tenant_id)
    except Exception as e:
        raise map_boto_error(e) from e

    ecs_clusters = [r for r in resources if ":cluster/" in (r["arn"] or "")]
    ecs_services = [r for r in resources if ":service/" in (r["arn"] or "")]
    target_groups = [r for r in resources if ":targetgroup/" in (r["arn"] or "")]
    cfn_stacks = [r for r in resources if ":stack/" in (r["arn"] or "")]

    for r in ecs_clusters + ecs_services + target_groups + cfn_stacks:
        assert_resource_belongs(tenant_id, r["arn"], r["tags"])

    services_out: list[dict] = []
    try:
        ecs = _client("ecs")
        for cluster in ecs_clusters:
            cluster_arn = cluster["arn"]
            svc_arns_for_cluster = [
                s["arn"] for s in ecs_services
                if cluster_arn.rsplit("/", 1)[-1] in s["arn"]
            ]
            services_out.extend(_ecs_state(ecs, cluster_arn, svc_arns_for_cluster))
    except Exception as e:
        raise map_boto_error(e) from e

    targets_out: list[dict] = []
    try:
        elbv2 = _client("elbv2")
        targets_out = _tg_health(elbv2, [tg["arn"] for tg in target_groups])
    except Exception as e:
        raise map_boto_error(e) from e

    deploys_out: list[dict] = []
    try:
        cfn = _client("cloudformation")
        for stack in cfn_stacks:
            stack_name = stack["arn"].rsplit("/", 2)[-2] if "/" in stack["arn"] else stack["arn"]
            deploys_out.append({
                "stack_name": stack_name,
                "recent_events": _recent_cfn_events(cfn, stack_name, limit=5),
            })
    except Exception as e:
        raise map_boto_error(e) from e

    result = {
        "tenant_id": tenant_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "ecs_clusters": [c["arn"] for c in ecs_clusters],
        "ecs_services": services_out,
        "alb_targets": targets_out,
        "cfn_stacks": deploys_out,
    }

    write_audit_event(
        tenant_id=tenant_id,
        tool_name=TOOL_NAME,
        resource_arns=[r["arn"] for r in resources],
        result_count=(
            len(services_out) + len(targets_out) + len(deploys_out)
        ),
    )
    return result


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read the operational state of a customer tenant: ECS services, "
            "ALB target health, recent CloudFormation events. Tenant-scoped via "
            "TenantId tag with cross-tenant assertions and audit logging."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

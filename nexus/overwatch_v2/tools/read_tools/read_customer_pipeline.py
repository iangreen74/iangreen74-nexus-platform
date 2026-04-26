"""Phase 1 read_customer_pipeline — recent deploy activity for a tenant.

Reads CodeBuild projects + builds tagged for the tenant, plus tenant-
scoped CloudFormation stack events. Tenant-scoped via TenantId tag
(path γ + three guardrails — see _tenant_scope.py).
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


TOOL_NAME = "read_customer_pipeline"
MAX_BUILDS = 20

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {
            "type": "string",
            "description": "Full tenant ID (e.g. forge-1dba4143ca24ed1f).",
        },
        "limit": {
            "type": "integer",
            "description": f"Max recent builds per project (default 5, cap {MAX_BUILDS}).",
        },
    },
    "required": ["tenant_id"],
}


def _project_name(arn: str) -> str:
    return arn.rsplit("/", 1)[-1] if "/" in arn else arn.rsplit(":", 1)[-1]


def _builds_for_project(client, project_name: str, limit: int) -> list[dict]:
    try:
        resp = client.list_builds_for_project(projectName=project_name)
    except Exception:
        return []
    build_ids = (resp.get("ids") or [])[:limit]
    if not build_ids:
        return []
    try:
        details = client.batch_get_builds(ids=build_ids).get("builds", []) or []
    except Exception:
        return []
    out: list[dict] = []
    for b in details:
        out.append({
            "id": b.get("id"),
            "build_number": b.get("buildNumber"),
            "status": b.get("buildStatus"),
            "started": str(b.get("startTime")) if b.get("startTime") else None,
            "ended": str(b.get("endTime")) if b.get("endTime") else None,
            "duration_seconds": (
                int((b.get("endTime") - b.get("startTime")).total_seconds())
                if b.get("endTime") and b.get("startTime") else None
            ),
            "source_version": b.get("sourceVersion"),
            "resolved_source_version": b.get("resolvedSourceVersion"),
        })
    return out


def _stack_recent_changes(client, stack_name: str, limit: int = 10) -> list[dict]:
    try:
        resp = client.describe_stack_events(StackName=stack_name)
    except Exception:
        return []
    events = resp.get("StackEvents") or []
    deploy_terminal = [
        ev for ev in events
        if (ev.get("ResourceStatus") or "").endswith("_COMPLETE")
        or (ev.get("ResourceStatus") or "").endswith("_FAILED")
    ]
    out: list[dict] = []
    for ev in deploy_terminal[:limit]:
        if ev.get("ResourceType") != "AWS::CloudFormation::Stack":
            continue
        out.append({
            "timestamp": str(ev.get("Timestamp")),
            "status": ev.get("ResourceStatus"),
            "reason": (ev.get("ResourceStatusReason") or "")[:200],
        })
    return out


def handler(**params: Any) -> dict:
    tenant_id = require_tenant_id(params.get("tenant_id"))
    limit = max(1, min(int(params.get("limit") or 5), MAX_BUILDS))
    from nexus.aws_client import _client
    try:
        resources = list_tenant_resources(tenant_id)
    except Exception as e:
        raise map_boto_error(e) from e

    cb_projects = [r for r in resources if ":project/" in (r["arn"] or "")]
    cfn_stacks = [r for r in resources if ":stack/" in (r["arn"] or "")]
    for r in cb_projects + cfn_stacks:
        assert_resource_belongs(tenant_id, r["arn"], r["tags"])

    builds_out: list[dict] = []
    try:
        cb = _client("codebuild")
        for project in cb_projects:
            project_name = _project_name(project["arn"])
            builds_out.append({
                "project_name": project_name,
                "project_arn": project["arn"],
                "recent_builds": _builds_for_project(cb, project_name, limit),
            })
    except Exception as e:
        raise map_boto_error(e) from e

    deploys_out: list[dict] = []
    try:
        cfn = _client("cloudformation")
        for stack in cfn_stacks:
            stack_name = stack["arn"].rsplit("/", 2)[-2] if "/" in stack["arn"] else stack["arn"]
            deploys_out.append({
                "stack_name": stack_name,
                "recent_terminal_events": _stack_recent_changes(cfn, stack_name, limit=limit),
            })
    except Exception as e:
        raise map_boto_error(e) from e

    result = {
        "tenant_id": tenant_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "codebuild_projects": builds_out,
        "cloudformation_stacks": deploys_out,
    }

    write_audit_event(
        tenant_id=tenant_id,
        tool_name=TOOL_NAME,
        resource_arns=[r["arn"] for r in resources if r["arn"]],
        result_count=sum(len(p.get("recent_builds", [])) for p in builds_out)
                     + sum(len(s.get("recent_terminal_events", [])) for s in deploys_out),
    )
    return result


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read recent deploy activity for a customer tenant: CodeBuild "
            "builds + CloudFormation stack terminal events. Tenant-scoped "
            "via TenantId tag with cross-tenant assertions and audit logging."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

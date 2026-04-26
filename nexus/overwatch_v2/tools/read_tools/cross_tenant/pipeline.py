"""Tool 9 — read_customer_pipeline: CodeBuild + Step Functions per tenant.

Same Path γ guardrails. Filters by naming pattern; asserts; audits.
GitHub Actions intentionally deferred to Phase 0a (it requires the
GitHub App auth scope already shipped in PR #18, but the per-tenant
GH-Actions-runs read tool is a Phase 0a deliverable).
"""
from __future__ import annotations

from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)
from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    _audit_cross_tenant_call, _assert_tenant_scoped, _validate_tenant_id,
)


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {"type": "string", "description": "Forgewing tenant ID, e.g. forge-1dba4143ca24ed1f"},
        "limit": {"type": "integer", "description": "Max recent items per source (default 10, cap 50)"},
    },
    "required": ["tenant_id"],
}


def _client(service: str):
    from nexus.aws_client import _client as factory
    return factory(service)


def _read_codebuild_projects(short: str) -> list[dict[str, Any]]:
    cb = _client("codebuild")
    expected_prefix = f"forgescaler-forge-{short}-"
    try:
        names: list[str] = []
        for page in cb.get_paginator("list_projects").paginate():
            names.extend(page.get("projects") or [])
    except Exception as e:
        raise map_boto_error(e) from e
    matched = [n for n in names if n.startswith(expected_prefix)]
    out: list[dict[str, Any]] = []
    if matched:
        details = cb.batch_get_projects(names=matched).get("projects", [])
        for p in details:
            out.append({
                "name": p.get("name"),
                "service_role": p.get("serviceRole", "").split("/")[-1],
                "last_modified": p.get("lastModified").isoformat()
                    if p.get("lastModified") else None,
            })
    _assert_tenant_scoped(out, f"forge-{short}", "name")
    return out


def _read_recent_codebuild_runs(short: str, limit: int) -> list[dict[str, Any]]:
    cb = _client("codebuild")
    expected_prefix = f"forgescaler-forge-{short}-"
    try:
        names: list[str] = []
        for page in cb.get_paginator("list_projects").paginate():
            names.extend(page.get("projects") or [])
    except Exception as e:
        raise map_boto_error(e) from e
    matched = [n for n in names if n.startswith(expected_prefix)]
    if not matched:
        return []
    out: list[dict[str, Any]] = []
    for project in matched:
        try:
            ids = cb.list_builds_for_project(
                projectName=project, sortOrder="DESCENDING",
            ).get("ids", [])[:limit]
        except Exception:
            continue
        if not ids:
            continue
        builds = cb.batch_get_builds(ids=ids).get("builds", [])
        for b in builds:
            run = {
                "name": b.get("projectName"),
                "build_id": b.get("id"),
                "status": b.get("buildStatus"),
                "started_at": b.get("startTime").isoformat()
                    if b.get("startTime") else None,
                "duration_s": b.get("endTime") and b.get("startTime")
                    and (b["endTime"] - b["startTime"]).total_seconds(),
            }
            out.append(run)
    _assert_tenant_scoped(out, f"forge-{short}", "name")
    return out[:limit]


def handler(**params: Any) -> dict[str, Any]:
    tenant_id = params.get("tenant_id", "")
    limit = max(1, min(int(params.get("limit") or 10), 50))
    try:
        short = _validate_tenant_id(tenant_id)
    except ValueError as e:
        raise ToolUnknown(str(e)) from e
    resources_read: list[str] = []
    try:
        projects = _read_codebuild_projects(short)
        runs = _read_recent_codebuild_runs(short, limit)
    except Exception as e:
        _audit_cross_tenant_call(
            tenant_id, "read_customer_pipeline",
            resources_read, 0, error=str(e),
        )
        if isinstance(e, (ToolUnknown, AssertionError)):
            raise
        raise map_boto_error(e) from e
    resources_read = [p["name"] for p in projects] + \
                     [r["name"] for r in runs]
    result_count = len(projects) + len(runs)
    _audit_cross_tenant_call(
        tenant_id, "read_customer_pipeline", resources_read, result_count,
    )
    from datetime import datetime, timezone
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "codebuild_projects": projects,
        "recent_runs": runs,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_customer_pipeline",
        description=(
            "Phase 0c cross-tenant read. Returns CodeBuild projects and recent "
            "build runs for a Forgewing tenant. Filters by naming pattern; "
            "asserts tenant scope; audits. GitHub Actions read deferred to "
            "Phase 0a integration."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

"""Report: Pipeline Activity (last 24h, raw status).

Strategy:
  1. Enumerate tenants (same path as fleet_health).
  2. Per tenant, call ``read_customer_pipeline`` to fetch recent
     CodeBuild builds + CFN terminal events.
  3. Aggregate by raw status (SUCCEEDED / FAILED / IN_PROGRESS /
     other) over the last 24h window.

Semantic-failure-type grouping (build error vs deploy error vs smoke
fail vs rollback) is deferred — that's the Mechanism 2 classifier
substrate. We surface raw counts only.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


WINDOW_HOURS = 24


def _enumerate_tenants() -> list[str]:
    from nexus.aws_client import _client
    client = _client("resourcegroupstaggingapi")
    seen: set[str] = set()
    pages = client.get_paginator("get_resources").paginate(
        ResourceTypeFilters=["ecs:cluster"],
        TagFilters=[{"Key": "TenantId"}],
    )
    for page in pages:
        for entry in page.get("ResourceTagMappingList", []) or []:
            for tag in entry.get("Tags", []) or []:
                if tag.get("Key") == "TenantId" and tag.get("Value"):
                    seen.add(tag["Value"])
    return sorted(seen)


def _parse_started(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # CodeBuild timestamps come back stringified by read_customer_pipeline.
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("+0", 1)[0])
    except Exception:
        return None


def build(params: dict, tool_ctx) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    tenant_ids = _enumerate_tenants()
    by_status: Counter[str] = Counter()
    failed_rows: list[dict] = []
    per_tenant: list[dict] = []
    total_in_window = 0

    for tid in tenant_ids:
        try:
            pipeline = tool_ctx.read_customer_pipeline(tenant_id=tid, limit=10)
        except Exception as e:
            per_tenant.append({"tenant_id": tid, "error": str(e)[:200],
                               "builds_in_window": 0})
            continue

        tenant_count = 0
        for project in pipeline.get("codebuild_projects") or []:
            for build_ in project.get("recent_builds") or []:
                started = _parse_started(build_.get("started"))
                if started is None or started < cutoff.replace(tzinfo=None):
                    continue
                status = (build_.get("status") or "UNKNOWN").upper()
                by_status[status] += 1
                tenant_count += 1
                total_in_window += 1
                if status not in ("SUCCEEDED", "IN_PROGRESS"):
                    failed_rows.append({
                        "tenant_id": tid,
                        "project": project.get("project_name"),
                        "build_id": build_.get("id"),
                        "status": status,
                        "started": build_.get("started"),
                        "duration_seconds": build_.get("duration_seconds"),
                        "source_version": build_.get("source_version"),
                    })
        per_tenant.append({"tenant_id": tid, "builds_in_window": tenant_count})

    succeeded = by_status.get("SUCCEEDED", 0)
    success_rate = (succeeded / total_in_window) if total_in_window else None

    return [
        {"title": f"Last {WINDOW_HOURS}h totals", "kind": "metric",
         "data": {
             "total_builds": total_in_window,
             "by_status": dict(by_status),
             "success_rate": round(success_rate, 3) if success_rate is not None else None,
         }},
        {"title": "Per-tenant build counts", "kind": "table",
         "data": {"columns": ["tenant_id", "builds_in_window"],
                  "rows": per_tenant}},
        {"title": "Failed / non-success builds", "kind": "table",
         "data": {"columns": ["tenant_id", "project", "build_id", "status",
                              "started", "duration_seconds", "source_version"],
                  "rows": failed_rows}},
        {"title": "Semantic failure-type grouping", "kind": "text",
         "data": {"text": (
             "Deferred: classifier-based grouping (build error / deploy "
             "error / smoke fail / rollback) requires Mechanism 2 "
             "classifier output. Current view groups by raw CodeBuild "
             "status only.")}},
    ]

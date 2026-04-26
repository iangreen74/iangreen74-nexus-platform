"""Report: Tenant Operational Profile (single tenant, on-demand).

Maps directly onto the Phase 1 cross-tenant tools — this is the
cleanest report of the three feasible-now reports.

Sections:
  1. Identity + raw tenant_id (and tag-derived metadata if present)
  2. ECS service states
  3. ALB target health
  4. Recent deploys (CodeBuild builds + CFN terminal events)
  5. ARIA conversation activity (event count over last 4h)
  6. Ontology object counts (Forgewing graph; in-VPC only)
"""
from __future__ import annotations

from typing import Any


def _validate_tenant_id(params: dict) -> str:
    tid = params.get("tenant_id")
    if not isinstance(tid, str) or not tid:
        raise ValueError("tenant_id is required")
    if not tid.startswith("forge-"):
        raise ValueError("tenant_id must start with 'forge-'")
    return tid


def _safe_call(fn, **kw) -> tuple[Any, str | None]:
    """Call ``fn(**kw)``; return (result, None) or (None, err_summary)."""
    try:
        return fn(**kw), None
    except Exception as e:
        return None, str(e)[:300]


def build(params: dict, tool_ctx) -> list[dict]:
    tid = _validate_tenant_id(params)

    state, state_err = _safe_call(tool_ctx.read_customer_tenant_state, tenant_id=tid)
    pipeline, pipeline_err = _safe_call(
        tool_ctx.read_customer_pipeline, tenant_id=tid, limit=10,
    )
    convs, convs_err = _safe_call(
        tool_ctx.read_aria_conversations, tenant_id=tid, lookback_hours=4,
        max_events=50,
    )
    ontology, ontology_err = _safe_call(tool_ctx.read_customer_ontology, tenant_id=tid)

    sections: list[dict] = [
        {"title": "Identity", "kind": "metric",
         "data": {"tenant_id": tid,
                  "captured_at": (state or {}).get("captured_at"),
                  "tag_managed_by": "ForgeScaler"}},
    ]

    if state_err:
        sections.append({"title": "ECS / ALB / CFN", "kind": "text",
                         "data": {"text": f"unavailable: {state_err}"}})
    else:
        sections.append({"title": "ECS services", "kind": "table",
                         "data": {"columns": ["name", "status", "desired",
                                              "running", "rollout_state"],
                                  "rows": state.get("ecs_services") or []}})
        sections.append({"title": "ALB target health", "kind": "table",
                         "data": {"columns": ["tg_name", "healthy_count",
                                              "unhealthy_count", "states"],
                                  "rows": state.get("alb_targets") or []}})
        sections.append({"title": "Recent CFN events", "kind": "list",
                         "data": {"items": [
                             {"stack": s.get("stack_name"),
                              "events": s.get("recent_events", [])[:3]}
                             for s in (state.get("cfn_stacks") or [])
                         ]}})

    if pipeline_err:
        sections.append({"title": "Recent deploys", "kind": "text",
                         "data": {"text": f"unavailable: {pipeline_err}"}})
    else:
        rows: list[dict] = []
        for project in pipeline.get("codebuild_projects") or []:
            for b in project.get("recent_builds") or []:
                rows.append({
                    "project": project.get("project_name"),
                    "build_id": b.get("id"),
                    "status": b.get("status"),
                    "started": b.get("started"),
                    "duration_seconds": b.get("duration_seconds"),
                    "source_version": b.get("source_version"),
                })
        sections.append({"title": "Recent deploys", "kind": "table",
                         "data": {"columns": ["project", "build_id", "status",
                                              "started", "duration_seconds",
                                              "source_version"],
                                  "rows": rows}})

    if convs_err:
        sections.append({"title": "ARIA conversation activity", "kind": "text",
                         "data": {"text": f"unavailable: {convs_err}"}})
    else:
        sections.append({"title": "ARIA conversation activity", "kind": "metric",
                         "data": {"total_events_4h": convs.get("total_events", 0),
                                  "by_log_group": {
                                      lg: len(events)
                                      for lg, events in (
                                          convs.get("events_by_log_group") or {}
                                      ).items()
                                  }}})

    if ontology_err:
        sections.append({"title": "Ontology object counts", "kind": "text",
                         "data": {"text": f"unavailable: {ontology_err}"}})
    else:
        sections.append({"title": "Ontology object counts", "kind": "metric",
                         "data": ontology.get("counts") or {}})

    return sections

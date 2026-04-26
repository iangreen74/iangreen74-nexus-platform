"""Report: Fleet Health Overview (current state).

Strategy:
  1. Enumerate tenants by querying tagged resources for ``TenantId``
     via ``list_aws_resources`` (or the resourcegroupstaggingapi
     directly). We use the ECS clusters as the canonical tenant list.
  2. For each tenant, call ``read_customer_tenant_state`` and bucket
     into Green / Amber / Red based on running-vs-desired counts and
     ALB target health.

Trend over 7 days is deferred until snapshot history exists; the
current envelope reports point-in-time state only.
"""
from __future__ import annotations

from collections import Counter
from typing import Any


def _classify(state: dict) -> str:
    """Return 'green' | 'amber' | 'red' based on a tenant_state envelope."""
    services = state.get("ecs_services") or []
    targets = state.get("alb_targets") or []
    if not services and not targets:
        return "amber"  # we have a tenant but no observable services -> ambiguous

    any_red = False
    any_amber = False
    for svc in services:
        desired = int(svc.get("desired") or 0)
        running = int(svc.get("running") or 0)
        if desired > 0 and running == 0:
            any_red = True
        elif desired > 0 and running < desired:
            any_amber = True
    for tg in targets:
        h = int(tg.get("healthy_count") or 0)
        u = int(tg.get("unhealthy_count") or 0)
        if h == 0 and u > 0:
            any_red = True
        elif u > 0:
            any_amber = True
    if any_red:
        return "red"
    if any_amber:
        return "amber"
    return "green"


def _enumerate_tenants(tool_ctx) -> list[str]:
    """Find tenant IDs by querying tagged ECS clusters.

    Uses the same Resource Groups Tagging API path the cross-tenant
    tools rely on, but with no TenantId filter — returns the union of
    distinct ``TenantId`` tag values across all tagged ECS clusters.
    """
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


def build(params: dict, tool_ctx) -> list[dict]:
    tenant_ids = _enumerate_tenants(tool_ctx)

    bucket = Counter({"green": 0, "amber": 0, "red": 0})
    rows: list[dict] = []
    troubled: list[dict] = []
    for tid in tenant_ids:
        try:
            state = tool_ctx.read_customer_tenant_state(tenant_id=tid)
        except Exception as e:
            bucket["red"] += 1
            rows.append({"tenant_id": tid, "status": "red", "error": str(e)[:200]})
            troubled.append({"tenant_id": tid, "reason": str(e)[:120]})
            continue
        status = _classify(state)
        bucket[status] += 1
        unhealthy_targets = sum(
            int(tg.get("unhealthy_count") or 0) for tg in (state.get("alb_targets") or [])
        )
        rows.append({
            "tenant_id": tid,
            "status": status,
            "ecs_services": len(state.get("ecs_services") or []),
            "alb_targets_healthy": sum(
                int(tg.get("healthy_count") or 0)
                for tg in (state.get("alb_targets") or [])
            ),
            "alb_targets_unhealthy": unhealthy_targets,
        })
        if status != "green":
            troubled.append({
                "tenant_id": tid, "status": status,
                "unhealthy_targets": unhealthy_targets,
            })

    return [
        {"title": "Fleet totals", "kind": "metric",
         "data": {"total": len(tenant_ids), **bucket}},
        {"title": "Per-tenant status", "kind": "table",
         "data": {"columns": ["tenant_id", "status", "ecs_services",
                              "alb_targets_healthy", "alb_targets_unhealthy"],
                  "rows": rows}},
        {"title": "Top troubled tenants", "kind": "list",
         "data": {"items": troubled[:5]}},
        {"title": "7-day trend", "kind": "text",
         "data": {"text": (
             "Trend deferred: requires ontology snapshot history. "
             "Current implementation is point-in-time only.")}},
    ]

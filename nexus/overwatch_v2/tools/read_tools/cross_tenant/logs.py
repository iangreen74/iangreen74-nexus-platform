"""Tool 10 — read_customer_logs: bounded CloudWatch logs per tenant.

Reads only log groups whose name contains the tenant's short ID. Time-
window-bounded (default last 60 min, max 24h). Same Path γ guardrails.

NOTE: today's Forgewing tenants do not yet have log groups under a
forgescaler-forge-{short}-* naming convention — this tool returns an
empty list against most tenants. Once the per-tenant logging
convention lands, the tool surface is ready.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)
from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    AUDIT_LOG_GROUP, _audit_cross_tenant_call, _assert_tenant_scoped,
    _validate_tenant_id,
)


MAX_WINDOW_MINUTES = 24 * 60
DEFAULT_WINDOW_MINUTES = 60
MAX_EVENTS_PER_GROUP = 200

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {"type": "string", "description": "Forgewing tenant ID"},
        "log_group_pattern": {
            "type": "string",
            "description": (
                "Optional substring to filter the matched log groups. "
                "Only applied within the tenant-scoped set."
            ),
        },
        "time_range_minutes": {
            "type": "integer",
            "description": f"Lookback window. Default {DEFAULT_WINDOW_MINUTES}, capped at {MAX_WINDOW_MINUTES}.",
        },
    },
    "required": ["tenant_id"],
}


def _client(service: str):
    from nexus.aws_client import _client as factory
    return factory(service)


def _matched_log_groups(short: str, pattern: str) -> list[str]:
    """List CW log groups whose name contains the short tenant ID and the
    optional substring pattern. Excludes the audit group itself.
    """
    logs = _client("logs")
    matched: list[str] = []
    for page in logs.get_paginator("describe_log_groups").paginate():
        for g in page.get("logGroups") or []:
            name = g.get("logGroupName") or ""
            if name == AUDIT_LOG_GROUP:
                continue
            if short not in name:
                continue
            if pattern and pattern not in name:
                continue
            matched.append(name)
    return matched


def handler(**params: Any) -> dict[str, Any]:
    tenant_id = params.get("tenant_id", "")
    pattern = params.get("log_group_pattern", "") or ""
    minutes = max(1, min(int(params.get("time_range_minutes") or DEFAULT_WINDOW_MINUTES),
                         MAX_WINDOW_MINUTES))
    try:
        short = _validate_tenant_id(tenant_id)
    except ValueError as e:
        raise ToolUnknown(str(e)) from e
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    resources_read: list[str] = []
    try:
        groups = _matched_log_groups(short, pattern)
        events_by_group: dict[str, list[dict[str, Any]]] = {}
        logs = _client("logs")
        for g in groups:
            try:
                resp = logs.filter_log_events(
                    logGroupName=g, startTime=start_ms, endTime=end_ms,
                    limit=MAX_EVENTS_PER_GROUP,
                )
                events_by_group[g] = [
                    {"timestamp": e.get("timestamp"),
                     "message": (e.get("message") or "")[:1000]}
                    for e in resp.get("events", [])
                ]
            except Exception:
                events_by_group[g] = []
            resources_read.append(g)
        # Defence in depth: ensure no group leaked outside the tenant short
        _assert_tenant_scoped(
            [{"name": f"forgescaler-forge-{short}-_logs_marker"}] +
            [{"name": f"forgescaler-forge-{short}-_g_{g}"} for g in groups],
            tenant_id, "name",
        )
    except Exception as e:
        _audit_cross_tenant_call(
            tenant_id, "read_customer_logs",
            resources_read, 0, error=str(e),
        )
        if isinstance(e, (ToolUnknown, AssertionError)):
            raise
        raise map_boto_error(e) from e
    result_count = sum(len(v) for v in events_by_group.values())
    _audit_cross_tenant_call(
        tenant_id, "read_customer_logs", resources_read, result_count,
    )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "window_minutes": minutes,
        "log_groups_scanned": groups,
        "events": events_by_group,
        "captured_at": end.isoformat().replace("+00:00", "Z"),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_customer_logs",
        description=(
            "Phase 0c cross-tenant read. Returns CloudWatch log events from "
            "log groups whose name contains the tenant's short ID. Time-"
            "window-bounded (default 60 min, max 24h). Asserts tenant scope; "
            "audits."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

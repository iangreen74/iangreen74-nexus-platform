"""Phase 1 read_aria_conversations — ARIA daemon conversation log filter.

CloudWatch FilterLogEvents over /aria/daemon and /aria/console scoped
by substring match on tenant_id. Bounded time window (24h cap) and
event count (200) so a wide net doesn't cost a fortune.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools._tenant_scope import (
    require_tenant_id,
    write_audit_event,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_aria_conversations"
LOG_GROUPS = ("/aria/daemon", "/aria/console")
MAX_WINDOW_HOURS = 24
MAX_EVENTS = 200

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {
            "type": "string",
            "description": "Full tenant ID (e.g. forge-1dba4143ca24ed1f).",
        },
        "lookback_hours": {
            "type": "integer",
            "description": (
                f"Hours back from now (default 4, cap {MAX_WINDOW_HOURS})."
            ),
        },
        "max_events": {
            "type": "integer",
            "description": (
                f"Max events to return per log group (default 50, cap {MAX_EVENTS})."
            ),
        },
    },
    "required": ["tenant_id"],
}


def _filter(client, log_group: str, tenant_id: str,
            start_ms: int, end_ms: int, max_events: int) -> list[dict]:
    try:
        resp = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_ms,
            endTime=end_ms,
            filterPattern=f'"{tenant_id}"',
            limit=max_events,
        )
    except client.exceptions.ResourceNotFoundException:
        return []
    except Exception:
        return []
    events = (resp.get("events") or [])[:max_events]
    return [
        {
            "timestamp": ev.get("timestamp"),
            "log_stream": ev.get("logStreamName"),
            "message": (ev.get("message") or "")[:2000],
        }
        for ev in events
    ]


def handler(**params: Any) -> dict:
    tenant_id = require_tenant_id(params.get("tenant_id"))
    lookback_hours = max(1, min(int(params.get("lookback_hours") or 4), MAX_WINDOW_HOURS))
    max_events = max(1, min(int(params.get("max_events") or 50), MAX_EVENTS))

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=lookback_hours)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    from nexus.aws_client import _client
    try:
        logs = _client("logs")
    except Exception as e:
        raise map_boto_error(e) from e

    by_group: dict[str, list[dict]] = {}
    total = 0
    for lg in LOG_GROUPS:
        events = _filter(logs, lg, tenant_id, start_ms, end_ms, max_events)
        by_group[lg] = events
        total += len(events)

    result = {
        "tenant_id": tenant_id,
        "captured_at": end.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "lookback_hours": lookback_hours,
        "events_by_log_group": by_group,
        "total_events": total,
    }

    write_audit_event(
        tenant_id=tenant_id,
        tool_name=TOOL_NAME,
        resource_arns=[
            f"arn:aws:logs:us-east-1:418295677815:log-group:{lg}"
            for lg in LOG_GROUPS
        ],
        result_count=total,
    )
    return result


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read ARIA daemon and console log events mentioning the given "
            "tenant_id. Substring filter; bounded window (cap 24h) and "
            "event count (cap 200/group)."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

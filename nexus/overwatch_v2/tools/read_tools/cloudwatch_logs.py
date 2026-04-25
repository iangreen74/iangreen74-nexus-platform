"""Tool 2 — read_cloudwatch_logs: bounded log retrieval via FilterLogEvents.

Time-window cap (24 hours) is the safety. A reasoner asking for 30 days of
logs gets the cap, not a $200 CloudWatch bill.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


MAX_WINDOW_HOURS = 24
MAX_EVENTS = 1000

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "log_group": {"type": "string"},
        "start_time": {"type": "string",
                       "description": "ISO-8601 datetime (e.g., 2026-04-24T00:00:00Z)."},
        "end_time": {"type": "string",
                     "description": "ISO-8601 datetime. Capped to start_time + 24h."},
        "filter_pattern": {"type": "string",
                           "description": "CloudWatch Logs filter pattern (optional)."},
        "max_events": {"type": "integer",
                       "description": f"Hard cap {MAX_EVENTS}; default 100."},
    },
    "required": ["log_group", "start_time", "end_time"],
}


def _parse(ts: str) -> datetime:
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def _bound_window(start: str, end: str) -> tuple[int, int, bool]:
    """Returns (start_ms, end_ms, capped). Caps end to start+24h if longer."""
    s = _parse(start)
    e = _parse(end)
    capped = False
    max_end = s + timedelta(hours=MAX_WINDOW_HOURS)
    if e > max_end:
        e = max_end
        capped = True
    return int(s.timestamp() * 1000), int(e.timestamp() * 1000), capped


def handler(**params: Any) -> dict:
    log_group = params["log_group"]
    start_ms, end_ms, capped = _bound_window(params["start_time"], params["end_time"])
    requested_max = int(params.get("max_events") or 100)
    if requested_max > MAX_EVENTS:
        raise ToolUnknown(f"max_events {requested_max} exceeds hard cap {MAX_EVENTS}")
    requested_max = max(1, min(requested_max, MAX_EVENTS))
    kwargs: dict[str, Any] = {
        "logGroupName": log_group,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": requested_max,
    }
    pattern = params.get("filter_pattern")
    if pattern:
        kwargs["filterPattern"] = pattern
    try:
        from nexus.aws_client import _client
        resp = _client("logs").filter_log_events(**kwargs)
    except Exception as e:
        raise map_boto_error(e) from e
    events = resp.get("events", []) or []
    return {
        "events": [
            {"timestamp": ev.get("timestamp"),
             "message": ev.get("message", "")[:4000],
             "log_stream": ev.get("logStreamName")}
            for ev in events[:requested_max]
        ],
        "total_count": len(events),
        "truncated": bool(resp.get("nextToken")) or len(events) > requested_max,
        "window_capped_to_24h": capped,
        "log_group": log_group,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_cloudwatch_logs",
        description=(
            "Filter log events from a CloudWatch log group. "
            f"Time window is hard-capped to {MAX_WINDOW_HOURS} hours; "
            f"max_events hard-capped to {MAX_EVENTS}."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

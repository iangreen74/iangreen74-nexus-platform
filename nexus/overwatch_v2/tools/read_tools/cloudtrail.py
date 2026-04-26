"""Phase 0b read_cloudtrail — structured CloudTrail LookupEvents wrapper.

Bounded by a 24h time window and 200-event cap (consistent with
``read_cloudwatch_logs`` ceilings). Filter helpers wrap the limited
CloudTrail attribute-key set: ``EventName``, ``Username``, ``ResourceName``,
``ResourceType``, ``EventSource``, ``ReadOnly``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_cloudtrail"
MAX_WINDOW_HOURS = 24
MAX_EVENTS = 200

ALLOWED_ATTRS = {
    "EventName", "Username", "ResourceName", "ResourceType",
    "EventSource", "ReadOnly", "AccessKeyId", "EventId",
}

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "filter": {
            "type": "object",
            "description": (
                "CloudTrail LookupAttribute as {key, value}. "
                f"Allowed keys: {sorted(ALLOWED_ATTRS)}."
            ),
        },
        "start_time": {"type": "string",
                       "description": "ISO-8601; default: now - 1h."},
        "end_time": {"type": "string",
                     "description": f"ISO-8601; capped to start + {MAX_WINDOW_HOURS}h."},
        "max_events": {"type": "integer",
                       "description": f"Default 50, hard cap {MAX_EVENTS}."},
    },
    "required": [],
}


def _parse(ts: str | None, default: datetime) -> datetime:
    if not ts:
        return default
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def _bound(start: datetime, end: datetime) -> tuple[datetime, datetime, bool]:
    capped = False
    max_end = start + timedelta(hours=MAX_WINDOW_HOURS)
    if end > max_end:
        end = max_end
        capped = True
    return start, end, capped


def handler(**params: Any) -> dict:
    now = datetime.now(timezone.utc)
    start = _parse(params.get("start_time"), now - timedelta(hours=1))
    end_raw = _parse(params.get("end_time"), now)
    start, end, capped = _bound(start, end_raw)

    requested_max = int(params.get("max_events") or 50)
    if requested_max > MAX_EVENTS:
        raise ToolUnknown(f"max_events {requested_max} exceeds hard cap {MAX_EVENTS}")
    requested_max = max(1, min(requested_max, MAX_EVENTS))

    kwargs: dict[str, Any] = {
        "StartTime": start, "EndTime": end, "MaxResults": min(50, requested_max),
    }
    f = params.get("filter") or {}
    if f:
        key = f.get("key") or f.get("AttributeKey")
        value = f.get("value") or f.get("AttributeValue")
        if key and key not in ALLOWED_ATTRS:
            raise ToolUnknown(
                f"filter.key {key!r} not in allowed set {sorted(ALLOWED_ATTRS)}"
            )
        if key and value:
            kwargs["LookupAttributes"] = [
                {"AttributeKey": key, "AttributeValue": str(value)},
            ]

    try:
        from nexus.aws_client import _client
        client = _client("cloudtrail")
        events: list[dict] = []
        paginator = client.get_paginator("lookup_events")
        for page in paginator.paginate(**kwargs):
            for ev in page.get("Events") or []:
                events.append({
                    "event_id": ev.get("EventId"),
                    "event_name": ev.get("EventName"),
                    "event_source": ev.get("EventSource"),
                    "username": ev.get("Username"),
                    "event_time": (ev.get("EventTime").isoformat()
                                   if ev.get("EventTime") else None),
                    "resources": [
                        {"type": r.get("ResourceType"),
                         "name": r.get("ResourceName")}
                        for r in (ev.get("Resources") or [])
                    ],
                    "read_only": ev.get("ReadOnly"),
                    "access_key_id": ev.get("AccessKeyId"),
                })
                if len(events) >= requested_max:
                    break
            if len(events) >= requested_max:
                break
    except Exception as e:
        raise map_boto_error(e) from e

    return {
        "events": events,
        "total_count": len(events),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_capped_to_24h": capped,
        "filter_applied": f or None,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Look up CloudTrail events. Window hard-capped to "
            f"{MAX_WINDOW_HOURS}h, events to {MAX_EVENTS}. "
            "Filter is one LookupAttribute (e.g. {key:'EventName', "
            "value:'AssumeRole'})."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

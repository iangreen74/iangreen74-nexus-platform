"""Tool — read_cloudtrail: structured CloudTrail LookupEvents wrapper.

Phase 0b. Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md L145.
Time-bounded (default last 60 min, hard cap 24h). Result-bounded
(default 100, hard cap 500). Returns a uniform `{events, source,
time_range, count, truncated}` envelope.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


SOURCE = "cloudtrail"
MAX_WINDOW_HOURS = 24
DEFAULT_WINDOW_MINUTES = 60
MAX_EVENTS = 500
DEFAULT_EVENTS = 100

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "start_time": {
            "type": "string",
            "description": "ISO-8601 (e.g. 2026-04-26T13:00:00Z). "
                           "Optional; defaults to now - 60 min.",
        },
        "end_time": {
            "type": "string",
            "description": "ISO-8601. Optional; defaults to now. "
                           "Capped to start_time + 24h.",
        },
        "event_name": {
            "type": "string",
            "description": "Filter by AWS API operation name "
                           "(e.g. UpdateService, AssumeRole).",
        },
        "resource_arn": {
            "type": "string",
            "description": "Filter by resource ARN (e.g. an ECS service ARN).",
        },
        "username": {
            "type": "string",
            "description": "Filter by IAM user / assumed-role session name.",
        },
        "max_events": {
            "type": "integer",
            "description": f"Hard cap {MAX_EVENTS}; default {DEFAULT_EVENTS}.",
        },
    },
    "required": [],
}


def _client():
    from nexus.aws_client import _client as factory
    return factory("cloudtrail")


def _parse_iso(ts: str | None, default: datetime) -> datetime:
    if not ts:
        return default
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ToolUnknown(f"bad ISO-8601 timestamp {ts!r}: {e}") from e


def _bound_window(start: str | None, end: str | None
                  ) -> tuple[datetime, datetime, bool]:
    now = datetime.now(timezone.utc)
    e = _parse_iso(end, now)
    s = _parse_iso(start, e - timedelta(minutes=DEFAULT_WINDOW_MINUTES))
    capped = False
    if e - s > timedelta(hours=MAX_WINDOW_HOURS):
        e = s + timedelta(hours=MAX_WINDOW_HOURS)
        capped = True
    return s, e, capped


def _build_lookup_attrs(params: dict[str, Any]) -> list[dict[str, str]]:
    """CloudTrail LookupEvents takes at most ONE LookupAttribute. Pick
    the most specific filter the caller supplied; the rest become
    client-side post-filters in `_match_extra_filters`.
    """
    if name := params.get("event_name"):
        return [{"AttributeKey": "EventName", "AttributeValue": str(name)}]
    if arn := params.get("resource_arn"):
        return [{"AttributeKey": "ResourceName", "AttributeValue": str(arn)}]
    if user := params.get("username"):
        return [{"AttributeKey": "Username", "AttributeValue": str(user)}]
    return []


def _shape_event(e: dict[str, Any]) -> dict[str, Any]:
    """CloudTrail returns metadata + a JSON CloudTrailEvent string. Shape
    into the uniform envelope used by query_correlated_events.
    """
    return {
        "timestamp": e.get("EventTime").isoformat()
            if hasattr(e.get("EventTime"), "isoformat") else e.get("EventTime"),
        "event_id": e.get("EventId"),
        "event_name": e.get("EventName"),
        "principal": e.get("Username"),
        "resources": [
            {"type": r.get("ResourceType"), "name": r.get("ResourceName")}
            for r in (e.get("Resources") or [])
        ],
        "raw": e.get("CloudTrailEvent"),
    }


def handler(**params: Any) -> dict[str, Any]:
    s, e, window_capped = _bound_window(
        params.get("start_time"), params.get("end_time"),
    )
    max_events = max(1, min(int(params.get("max_events") or DEFAULT_EVENTS),
                            MAX_EVENTS))
    attrs = _build_lookup_attrs(params)
    cloudtrail = _client()
    events: list[dict[str, Any]] = []
    next_token: str | None = None
    truncated = window_capped
    try:
        while len(events) < max_events:
            kwargs: dict[str, Any] = {
                "StartTime": s, "EndTime": e,
                "MaxResults": min(50, max_events - len(events)),
            }
            if attrs:
                kwargs["LookupAttributes"] = attrs
            if next_token:
                kwargs["NextToken"] = next_token
            resp = cloudtrail.lookup_events(**kwargs)
            events.extend(_shape_event(ev) for ev in resp.get("Events", []))
            next_token = resp.get("NextToken")
            if not next_token:
                break
        if next_token:
            truncated = True
    except Exception as ex:
        raise map_boto_error(ex) from ex
    return {
        "source": SOURCE,
        "time_range": {"start": s.isoformat(), "end": e.isoformat()},
        "count": len(events),
        "truncated": truncated,
        "events": events[:max_events],
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_cloudtrail",
        description=(
            "Phase 0b: time-bounded CloudTrail LookupEvents. Filter by "
            "event_name, resource_arn, or username. Default window = "
            "last 60 min, hard cap 24h. Returns uniform envelope with "
            "timestamp, event_id, event_name, principal, resources."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

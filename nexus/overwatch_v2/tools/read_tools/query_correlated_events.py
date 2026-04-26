"""Phase 0b query_correlated_events: cross-source time-aligned timeline.

Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md L145, "fan-out across the
above". Calls read_cloudtrail + read_alb_logs + read_cloudwatch_logs
within a `[ts - window, ts + window]` envelope and returns one
time-sorted array of records normalised into a uniform shape.

Echo composes by calling this single tool with one timestamp + window.
The audit log shows one correlation call rather than three opaque
read calls.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown


VALID_SOURCES = ("cloudtrail", "alb", "cloudwatch_logs")
MAX_WINDOW_SECONDS = 600
DEFAULT_WINDOW_SECONDS = 60
MAX_EVENTS = 500
DEFAULT_PER_SOURCE = 100

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "timestamp": {"type": "string",
                      "description": "ISO-8601 center timestamp."},
        "window_seconds": {"type": "integer",
                           "description": f"+/- this around timestamp. Default {DEFAULT_WINDOW_SECONDS}, max {MAX_WINDOW_SECONDS}."},
        "sources": {"type": "array",
                    "description": f"Subset of {list(VALID_SOURCES)}; default all three."},
        "log_group": {"type": "string",
                      "description": "Required when 'cloudwatch_logs' is in sources."},
        "filter_pattern": {"type": "string",
                           "description": "Optional CloudWatch Logs filter pattern."},
        "max_events": {"type": "integer",
                       "description": f"Total cap (across sources). Hard cap {MAX_EVENTS}."},
    },
    "required": ["timestamp"],
}


def _parse_iso(ts: str) -> datetime:
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError as e:
        raise ToolUnknown(f"bad ISO-8601 timestamp {ts!r}: {e}") from e


def _normalise(source: str, ev: dict[str, Any]) -> dict[str, Any]:
    """Map source-specific event shapes into the uniform correlator row."""
    if source == "cloudtrail":
        return {
            "source": "cloudtrail",
            "timestamp": ev.get("timestamp"),
            "summary": f"{ev.get('event_name')} by {ev.get('principal') or 'unknown'}",
            "principal": ev.get("principal"),
            "resource": ((ev.get("resources") or [{}])[0] or {}).get("name"),
            "raw": ev,
        }
    if source == "alb":
        req = (ev.get("request") or "").split(" ", 2)
        path = req[1] if len(req) >= 2 else ev.get("request")
        return {
            "source": "alb",
            "timestamp": ev.get("timestamp"),
            "summary": f"{ev.get('elb_status_code')} {req[0] if req else ''} {path}",
            "principal": ev.get("client_addr"),
            "resource": ev.get("target_group_arn"),
            "raw": ev,
        }
    # cloudwatch_logs
    return {
        "source": "cloudwatch_logs",
        "timestamp": ev.get("timestamp") or ev.get("ingestionTime"),
        "summary": (ev.get("message") or "")[:200],
        "principal": None,
        "resource": ev.get("log_group") or ev.get("logGroupName"),
        "raw": ev,
    }


def _ts_key(row: dict[str, Any]) -> str:
    """Sort key: prefer ISO string; fallback to numeric ms."""
    t = row.get("timestamp")
    if isinstance(t, (int, float)):
        return datetime.fromtimestamp(int(t) / 1000, tz=timezone.utc).isoformat()
    return str(t or "")


def _call_cloudtrail(s: datetime, e: datetime, cap: int) -> list[dict[str, Any]]:
    from nexus.overwatch_v2.tools.read_tools import read_cloudtrail
    r = read_cloudtrail.handler(
        start_time=s.isoformat(), end_time=e.isoformat(), max_events=cap,
    )
    return [_normalise("cloudtrail", ev) for ev in r.get("events", [])]


def _call_alb(s: datetime, e: datetime, cap: int) -> list[dict[str, Any]]:
    from nexus.overwatch_v2.tools.read_tools import read_alb_logs
    r = read_alb_logs.handler(
        start_time=s.isoformat(), end_time=e.isoformat(), max_events=cap,
    )
    return [_normalise("alb", ev) for ev in r.get("events", [])]


def _call_cwlogs(s: datetime, e: datetime, cap: int, log_group: str,
                 filter_pattern: str) -> list[dict[str, Any]]:
    if not log_group:
        return []
    from nexus.overwatch_v2.tools.read_tools import cloudwatch_logs
    r = cloudwatch_logs.handler(
        log_group=log_group,
        start_time=s.isoformat(), end_time=e.isoformat(),
        filter_pattern=filter_pattern or "",
        max_events=cap,
    )
    return [_normalise("cloudwatch_logs", {**ev, "log_group": log_group})
            for ev in r.get("events", [])]


def handler(**params: Any) -> dict[str, Any]:
    centre = _parse_iso(params["timestamp"])
    window = max(1, min(int(params.get("window_seconds") or DEFAULT_WINDOW_SECONDS),
                        MAX_WINDOW_SECONDS))
    sources = params.get("sources") or list(VALID_SOURCES)
    bad = [x for x in sources if x not in VALID_SOURCES]
    if bad:
        raise ToolUnknown(f"unknown source(s): {bad}; valid: {list(VALID_SOURCES)}")
    total_cap = max(1, min(int(params.get("max_events") or MAX_EVENTS), MAX_EVENTS))
    per_source = max(1, min(DEFAULT_PER_SOURCE, total_cap // max(1, len(sources)) or 1))
    s = centre - timedelta(seconds=window)
    e = centre + timedelta(seconds=window)
    rows: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    if "cloudtrail" in sources:
        ct = _call_cloudtrail(s, e, per_source)
        rows.extend(ct); by_source["cloudtrail"] = len(ct)
    if "alb" in sources:
        a = _call_alb(s, e, per_source)
        rows.extend(a); by_source["alb"] = len(a)
    if "cloudwatch_logs" in sources:
        c = _call_cwlogs(s, e, per_source,
                         params.get("log_group") or "",
                         params.get("filter_pattern") or "")
        rows.extend(c); by_source["cloudwatch_logs"] = len(c)
    rows.sort(key=_ts_key)
    truncated = len(rows) > total_cap
    return {
        "centre": centre.isoformat(),
        "window_seconds": window,
        "time_range": {"start": s.isoformat(), "end": e.isoformat()},
        "by_source": by_source,
        "count": min(len(rows), total_cap),
        "truncated": truncated,
        "events": rows[:total_cap],
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="query_correlated_events",
        description=(
            "Phase 0b: cross-source correlation primitive. Given a centre "
            "timestamp + window (default +/-60s, max +/-600s), fans out "
            "to read_cloudtrail / read_alb_logs / read_cloudwatch_logs "
            "and returns a single time-sorted array of normalised "
            "{source, timestamp, summary, principal, resource, raw} rows."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

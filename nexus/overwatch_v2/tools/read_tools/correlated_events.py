"""Phase 0b query_correlated_events — fan out across log sources.

Given an anchor timestamp and a window, pull events from CloudTrail,
CloudWatch Logs, and ALB access logs that overlap the window. Each
source's events are returned tagged with their source so the caller
can correlate by timestamp.

Acceptance per ``OPERATIONAL_TRUTH_SUBSTRATE.md``:
  Echo answers "what happened across all systems between 14:00 and
  14:30 today?" with one structured response covering CloudTrail +
  CloudWatch logs + ALB requests, evidence-cited per row.

Metric anomaly detection is a Phase 0d concern (operational graph);
this tool surfaces raw events from each source, not anomalies.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown


TOOL_NAME = "query_correlated_events"
SOURCE_CLOUDTRAIL = "cloudtrail"
SOURCE_CLOUDWATCH_LOGS = "cloudwatch_logs"
SOURCE_ALB_LOGS = "alb_logs"
ALL_SOURCES = (SOURCE_CLOUDTRAIL, SOURCE_CLOUDWATCH_LOGS, SOURCE_ALB_LOGS)

MAX_WINDOW_SECONDS = 3600          # 1h hard cap
DEFAULT_WINDOW_SECONDS = 600       # 10 min default
PER_SOURCE_EVENT_CAP = 50

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "timestamp": {"type": "string",
                      "description": "ISO-8601 anchor instant; default now."},
        "window_seconds": {"type": "integer",
                           "description": f"Default {DEFAULT_WINDOW_SECONDS}, cap {MAX_WINDOW_SECONDS}."},
        "sources": {"type": "array", "items": {"type": "string"},
                    "description": f"Subset of {list(ALL_SOURCES)}; default all."},
        "log_groups": {"type": "array", "items": {"type": "string"},
                       "description": "Required if cloudwatch_logs source is used."},
        "alb_bucket": {"type": "string",
                       "description": "Required if alb_logs source is used."},
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


def handler(**params: Any) -> dict:
    now = datetime.now(timezone.utc)
    anchor = _parse(params.get("timestamp"), now)
    window = int(params.get("window_seconds") or DEFAULT_WINDOW_SECONDS)
    if window > MAX_WINDOW_SECONDS:
        raise ToolUnknown(
            f"window_seconds {window} exceeds hard cap {MAX_WINDOW_SECONDS}"
        )
    half = timedelta(seconds=window / 2)
    start, end = anchor - half, anchor + half

    sources = list(params.get("sources") or ALL_SOURCES)
    bad = [s for s in sources if s not in ALL_SOURCES]
    if bad:
        raise ToolUnknown(f"unknown sources: {bad}")

    findings: dict[str, dict] = {}

    if SOURCE_CLOUDTRAIL in sources:
        from nexus.overwatch_v2.tools.read_tools import cloudtrail
        try:
            ct = cloudtrail.handler(
                start_time=start.isoformat(), end_time=end.isoformat(),
                max_events=PER_SOURCE_EVENT_CAP,
            )
            findings[SOURCE_CLOUDTRAIL] = {
                "events": ct.get("events") or [],
                "total_count": ct.get("total_count") or 0,
            }
        except Exception as e:
            findings[SOURCE_CLOUDTRAIL] = {"error": str(e)[:300], "events": []}

    if SOURCE_CLOUDWATCH_LOGS in sources:
        log_groups = params.get("log_groups") or []
        if not log_groups:
            findings[SOURCE_CLOUDWATCH_LOGS] = {
                "error": "log_groups required when cloudwatch_logs source is used",
                "events": [],
            }
        else:
            from nexus.overwatch_v2.tools.read_tools import cloudwatch_logs
            collected: list[dict] = []
            errors: dict[str, str] = {}
            for lg in log_groups:
                try:
                    cw = cloudwatch_logs.handler(
                        log_group=lg,
                        start_time=start.isoformat(),
                        end_time=end.isoformat(),
                        max_events=PER_SOURCE_EVENT_CAP,
                    )
                    for ev in (cw.get("events") or []):
                        collected.append({**ev, "log_group": lg})
                except Exception as e:
                    errors[lg] = str(e)[:300]
            findings[SOURCE_CLOUDWATCH_LOGS] = {
                "events": collected,
                "total_count": len(collected),
                "errors_by_log_group": errors,
            }

    if SOURCE_ALB_LOGS in sources:
        bucket = params.get("alb_bucket")
        if not bucket:
            findings[SOURCE_ALB_LOGS] = {
                "error": "alb_bucket required when alb_logs source is used",
                "records": [],
            }
        else:
            from nexus.overwatch_v2.tools.read_tools import alb_logs
            try:
                alb = alb_logs.handler(
                    bucket=bucket,
                    start_time=start.isoformat(),
                    end_time=end.isoformat(),
                    max_records=PER_SOURCE_EVENT_CAP,
                )
                findings[SOURCE_ALB_LOGS] = {
                    "records": alb.get("records") or [],
                    "total_count": alb.get("total_count") or 0,
                }
            except Exception as e:
                findings[SOURCE_ALB_LOGS] = {"error": str(e)[:300], "records": []}

    return {
        "anchor": anchor.isoformat(),
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_seconds": window,
        "sources_queried": sources,
        "findings": findings,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Fan out across CloudTrail + CloudWatch logs + ALB access logs "
            "for a time window around an anchor timestamp. Returns each "
            f"source's events tagged by source, capped at "
            f"{PER_SOURCE_EVENT_CAP} per source. Window cap "
            f"{MAX_WINDOW_SECONDS}s."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

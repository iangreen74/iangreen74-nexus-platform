"""Tool — read_cloudwatch_metrics: generalized CloudWatch metric reader.

Phase 0b §4. Spec: docs/OPERATIONAL_TRUTH_SUBSTRATE.md L145.

Companion to the existing namespace-scoped ``read_overwatch_metrics``
(Overwatch/V2 only). This one accepts any namespace + metric +
dimensions so the substrate can correlate against AWS-service
metrics (ALB, ECS, Lambda, RDS) during cross-source investigations.

Time-bounded (default last 60 min, hard cap 24h; longer windows
require an explicit ``allow_long_range`` opt-in up to 7 days).
Period floored at 60s (the smallest CloudWatch standard period).
Standard statistics (Average / Sum / Maximum / Minimum / SampleCount)
and extended percentiles (p50 / p90 / p95 / p99) both supported via
the same ``statistics`` parameter.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


SOURCE = "cloudwatch_metrics"
MAX_WINDOW_HOURS = 24
LONG_RANGE_MAX_DAYS = 7
DEFAULT_WINDOW_MINUTES = 60
MIN_PERIOD_SECONDS = 60
DEFAULT_PERIOD_SECONDS = 60
STANDARD_STATS = {"Average", "Sum", "Maximum", "Minimum", "SampleCount"}


def _is_extended(stat: str) -> bool:
    return (
        len(stat) >= 2 and stat[0] == "p"
        and stat[1:].replace(".", "", 1).isdigit()
    )


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string",
                      "description": "CloudWatch namespace, e.g. AWS/ApplicationELB."},
        "metric_name": {"type": "string",
                        "description": "Metric name within the namespace."},
        "dimensions": {"type": "object",
                       "description": "Dimension name -> value map."},
        "start_time": {"type": "string",
                       "description": "ISO-8601. Default now - 60 min."},
        "end_time": {"type": "string",
                     "description": "ISO-8601. Default now. Capped to start + 24h "
                                    "unless allow_long_range=true."},
        "period": {"type": "integer",
                   "description": f"Seconds; floor {MIN_PERIOD_SECONDS}, default {DEFAULT_PERIOD_SECONDS}."},
        "statistics": {"type": "array", "items": {"type": "string"},
                       "description": (f"Default ['Average']. Standard: "
                                       f"{sorted(STANDARD_STATS)}; extended: pNN.")},
        "allow_long_range": {"type": "boolean",
                             "description": "Permit > 24h window up to 7 days. Default false."},
    },
    "required": ["namespace", "metric_name"],
}


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


def _bound_window(start: str | None, end: str | None,
                  allow_long: bool) -> tuple[datetime, datetime, bool]:
    now = datetime.now(timezone.utc)
    e = _parse_iso(end, now)
    s = _parse_iso(start, e - timedelta(minutes=DEFAULT_WINDOW_MINUTES))
    if e < s:
        raise ToolUnknown(f"end_time {e.isoformat()} precedes start_time {s.isoformat()}")
    capped = False
    span = e - s
    short_max = timedelta(hours=MAX_WINDOW_HOURS)
    long_max = timedelta(days=LONG_RANGE_MAX_DAYS)
    if span > short_max and not allow_long:
        e = s + short_max
        capped = True
    elif span > long_max:
        e = s + long_max
        capped = True
    return s, e, capped


def _validate_stats(raw: list[str] | None) -> tuple[list[str], list[str]]:
    """Split into (standard_stats, extended_stats). Raise on unknowns or empty."""
    if raw is None:
        stats = ["Average"]
    else:
        stats = list(raw)
        if not stats:
            raise ToolUnknown("statistics may not be empty")
    standard: list[str] = []
    extended: list[str] = []
    for s in stats:
        if s in STANDARD_STATS:
            standard.append(s)
        elif _is_extended(s):
            extended.append(s)
        else:
            raise ToolUnknown(
                f"statistic {s!r} not in {sorted(STANDARD_STATS)} and not pNN"
            )
    return standard, extended


def handler(**params: Any) -> dict[str, Any]:
    namespace = params.get("namespace")
    metric_name = params.get("metric_name")
    if not namespace or not metric_name:
        raise ToolUnknown("namespace and metric_name are required")

    period = max(MIN_PERIOD_SECONDS, int(params.get("period") or DEFAULT_PERIOD_SECONDS))
    standard, extended = _validate_stats(params.get("statistics"))

    s, e, capped = _bound_window(
        params.get("start_time"), params.get("end_time"),
        bool(params.get("allow_long_range")),
    )

    dims = params.get("dimensions") or {}
    if not isinstance(dims, dict):
        raise ToolUnknown("dimensions must be a name->value object")
    dim_list = [{"Name": k, "Value": str(v)} for k, v in dims.items()]

    kwargs: dict[str, Any] = {
        "Namespace": namespace, "MetricName": metric_name,
        "Dimensions": dim_list, "StartTime": s, "EndTime": e, "Period": period,
    }
    if standard:
        kwargs["Statistics"] = standard
    if extended:
        kwargs["ExtendedStatistics"] = extended

    try:
        from nexus.aws_client import _client
        client = _client("cloudwatch")
        resp = client.get_metric_statistics(**kwargs)
    except Exception as ex:
        raise map_boto_error(ex) from ex

    points: list[dict[str, Any]] = []
    for p in resp.get("Datapoints") or []:
        ts = p.get("Timestamp")
        ts_iso = ts.isoformat() if ts else None
        unit = p.get("Unit")
        for stat in standard:
            if stat in p:
                points.append({"timestamp": ts_iso, "statistic": stat,
                               "value": p.get(stat), "unit": unit})
        ext = p.get("ExtendedStatistics") or {}
        for stat in extended:
            if stat in ext:
                points.append({"timestamp": ts_iso, "statistic": stat,
                               "value": ext.get(stat), "unit": unit})
    points.sort(key=lambda d: (d.get("timestamp") or "", d.get("statistic") or ""))

    return {
        "source": SOURCE,
        "namespace": namespace,
        "metric_name": metric_name,
        "dimensions": dims,
        "period": period,
        "statistics": standard + extended,
        "time_range": {"start": s.isoformat(), "end": e.isoformat()},
        "count": len(points),
        "truncated": capped,
        "datapoints": points,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_cloudwatch_metrics",
        description=(
            "Phase 0b: read CloudWatch metric datapoints from any namespace. "
            f"Window default {DEFAULT_WINDOW_MINUTES}m; cap {MAX_WINDOW_HOURS}h "
            f"(or {LONG_RANGE_MAX_DAYS}d with allow_long_range). Period floor "
            f"{MIN_PERIOD_SECONDS}s. Standard + extended (pNN) statistics."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

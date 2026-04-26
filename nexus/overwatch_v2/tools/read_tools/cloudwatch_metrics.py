"""Phase 0b read_cloudwatch_metrics — generalized CloudWatch metric reader.

The existing ``read_overwatch_metrics`` tool is namespace-scoped to
``Overwatch/V2``. This one accepts any namespace + metric + dimensions
so the substrate can correlate across AWS-service metrics (ALB,
Lambda, RDS, ECS) during cross-source investigations.

Bounded by a 24h window + period floor so a casual call doesn't
spam GetMetricStatistics with 1-second resolution over a week.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_cloudwatch_metrics"
MAX_WINDOW_HOURS = 24
MIN_PERIOD_SECONDS = 60
ALLOWED_STATS = {"SampleCount", "Average", "Sum", "Minimum", "Maximum",
                 "p50", "p90", "p95", "p99"}

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string",
                      "description": "CloudWatch namespace, e.g. AWS/ApplicationELB."},
        "metric_name": {"type": "string"},
        "dimensions": {"type": "object",
                       "description": "Dimension name -> value map."},
        "start_time": {"type": "string", "description": "ISO-8601; default now - 1h."},
        "end_time": {"type": "string",
                     "description": f"ISO-8601; capped to start + {MAX_WINDOW_HOURS}h."},
        "period_seconds": {"type": "integer",
                           "description": f"Default 300, floor {MIN_PERIOD_SECONDS}."},
        "statistic": {"type": "string", "enum": list(ALLOWED_STATS),
                      "description": "Default Average."},
    },
    "required": ["namespace", "metric_name"],
}


def _parse(ts: str | None, default: datetime) -> datetime:
    if not ts:
        return default
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def handler(**params: Any) -> dict:
    namespace = params.get("namespace")
    metric_name = params.get("metric_name")
    if not namespace or not metric_name:
        raise ToolUnknown("namespace and metric_name are required")

    statistic = params.get("statistic") or "Average"
    if statistic not in ALLOWED_STATS:
        raise ToolUnknown(f"statistic {statistic!r} not in {sorted(ALLOWED_STATS)}")

    period = max(MIN_PERIOD_SECONDS, int(params.get("period_seconds") or 300))

    now = datetime.now(timezone.utc)
    start = _parse(params.get("start_time"), now - timedelta(hours=1))
    end = _parse(params.get("end_time"), now)
    capped = False
    max_end = start + timedelta(hours=MAX_WINDOW_HOURS)
    if end > max_end:
        end = max_end
        capped = True

    dims = params.get("dimensions") or {}
    dim_list = [{"Name": k, "Value": v} for k, v in dims.items()]

    is_extended = statistic.startswith("p")  # p50/p90/...
    kwargs: dict[str, Any] = {
        "Namespace": namespace,
        "MetricName": metric_name,
        "Dimensions": dim_list,
        "StartTime": start,
        "EndTime": end,
        "Period": period,
    }
    if is_extended:
        kwargs["ExtendedStatistics"] = [statistic]
    else:
        kwargs["Statistics"] = [statistic]

    try:
        from nexus.aws_client import _client
        client = _client("cloudwatch")
        resp = client.get_metric_statistics(**kwargs)
    except Exception as e:
        raise map_boto_error(e) from e

    points = sorted(resp.get("Datapoints") or [], key=lambda d: d.get("Timestamp"))
    rows: list[dict] = []
    for p in points:
        ts = p.get("Timestamp")
        rows.append({
            "timestamp": ts.isoformat() if ts else None,
            "value": (p.get("ExtendedStatistics") or {}).get(statistic)
                     if is_extended else p.get(statistic),
            "unit": p.get("Unit"),
            "sample_count": p.get("SampleCount"),
        })

    return {
        "namespace": namespace,
        "metric_name": metric_name,
        "dimensions": dims,
        "statistic": statistic,
        "period_seconds": period,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_capped_to_24h": capped,
        "datapoints": rows,
        "total_count": len(rows),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read CloudWatch metric datapoints from any namespace. "
            f"Window {MAX_WINDOW_HOURS}h cap; period floor {MIN_PERIOD_SECONDS}s. "
            "Generalized counterpart to read_overwatch_metrics."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

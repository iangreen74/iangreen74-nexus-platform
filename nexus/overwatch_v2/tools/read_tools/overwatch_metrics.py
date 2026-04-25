"""Tool 6 — read_overwatch_metrics: cloudwatch:GetMetricStatistics for Overwatch/V2.

Used for the reasoner's introspection ("how am I performing?") and for
operator-facing dashboards on Day 6. Empty-result responses are valid
(no metrics emitted yet) — return an empty list, not an error.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


DEFAULT_NAMESPACE = "Overwatch/V2"
VALID_STATISTICS = {"Sum", "Average", "Maximum", "Minimum", "SampleCount"}

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "metric_name": {
            "type": "string",
            "description": "e.g., reasoner.tool_calls, reasoner.token_cost, "
                           "reasoner.proposal_count.",
        },
        "namespace": {"type": "string",
                      "description": f"default {DEFAULT_NAMESPACE!r}."},
        "start_time": {"type": "string", "description": "ISO-8601."},
        "end_time": {"type": "string", "description": "ISO-8601."},
        "period_seconds": {"type": "integer",
                           "description": "default 300, minimum 60."},
        "statistic": {"type": "string", "enum": sorted(VALID_STATISTICS),
                      "description": "default Sum"},
        "dimensions": {"type": "object",
                       "description": "name -> value pairs."},
    },
    "required": ["metric_name", "start_time", "end_time"],
}


def _parse(ts: str) -> datetime:
    s = ts.rstrip("Z")
    if "+" not in s and "-" not in s[10:]:
        s = s + "+00:00"
    return datetime.fromisoformat(s)


def handler(**params: Any) -> dict:
    metric = params["metric_name"]
    namespace = params.get("namespace") or DEFAULT_NAMESPACE
    period = int(params.get("period_seconds") or 300)
    if period < 60:
        raise ToolUnknown(f"period_seconds {period} < minimum 60")
    statistic = params.get("statistic") or "Sum"
    if statistic not in VALID_STATISTICS:
        raise ToolUnknown(f"statistic {statistic!r} not in {sorted(VALID_STATISTICS)}")
    start = _parse(params["start_time"])
    end = _parse(params["end_time"])
    if end <= start:
        raise ToolUnknown("end_time must be after start_time")
    dims = params.get("dimensions") or {}
    if not isinstance(dims, dict):
        raise ToolUnknown("dimensions must be an object")
    dim_list = [{"Name": str(k), "Value": str(v)} for k, v in dims.items()]
    try:
        from nexus.aws_client import _client
        resp = _client("cloudwatch").get_metric_statistics(
            Namespace=namespace, MetricName=metric, Dimensions=dim_list,
            StartTime=start, EndTime=end, Period=period, Statistics=[statistic],
        )
    except Exception as e:
        raise map_boto_error(e) from e
    points = sorted(resp.get("Datapoints", []) or [], key=lambda p: p.get("Timestamp"))
    return {
        "metric_name": metric, "namespace": namespace,
        "statistic": statistic, "period_seconds": period,
        "datapoints": [
            {"timestamp": str(p.get("Timestamp")),
             "value": p.get(statistic), "unit": p.get("Unit")}
            for p in points
        ],
        "count": len(points),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_overwatch_metrics",
        description=(
            "Read CloudWatch metrics from the Overwatch/V2 namespace. "
            "Used for reasoner introspection and operator dashboards. "
            "Returns empty datapoint list if no metrics emitted yet."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

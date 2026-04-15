"""
Bedrock Usage + Latency Monitor.

Reads AWS/Bedrock CloudWatch metrics: Invocations, InputTokenCount,
OutputTokenCount, InvocationLatency, InvocationClientErrors +
InvocationServerErrors. Aggregates over the last N hours and produces
a shaped summary for the Goal report + a dashboard endpoint.

Cost estimation uses published Bedrock on-demand prices for the two
models we actually call (Sonnet 4.6 and Haiku 4.5). Prices are in
MODEL_PRICING; update when AWS adjusts the rate card.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.bedrock_monitor")

NAMESPACE = "AWS/Bedrock"

# USD per 1K tokens as of 2026-04 — keep in sync with the AWS Bedrock
# pricing page. Rates are average of in/out for coarse estimation.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "sonnet": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "haiku":  {"input_per_1k": 0.001, "output_per_1k": 0.005},
}
_DEFAULT_MODEL = "sonnet"


def _cw():
    from nexus.aws_client import _client
    return _client("cloudwatch")


def _sum_metric(metric: str, start: datetime, end: datetime,
                 period_sec: int = 3600) -> float:
    try:
        resp = _cw().get_metric_statistics(
            Namespace=NAMESPACE, MetricName=metric,
            StartTime=start, EndTime=end,
            Period=period_sec, Statistics=["Sum"],
        ) or {}
    except Exception:
        logger.exception("bedrock sum %s failed", metric)
        return 0.0
    return sum(float(p.get("Sum") or 0) for p in resp.get("Datapoints", []) or [])


def _latency_pct(start: datetime, end: datetime) -> dict[str, float]:
    """p50/p90/p99 InvocationLatency across the window (ms → seconds)."""
    try:
        resp = _cw().get_metric_statistics(
            Namespace=NAMESPACE, MetricName="InvocationLatency",
            StartTime=start, EndTime=end, Period=3600,
            ExtendedStatistics=["p50", "p90", "p99"],
        ) or {}
    except Exception:
        logger.exception("bedrock latency query failed")
        return {}
    pts = resp.get("Datapoints", []) or []
    if not pts:
        return {}
    def _avg(key: str) -> float:
        vals = [float((p.get("ExtendedStatistics") or {}).get(key) or 0) for p in pts]
        vals = [v for v in vals if v > 0]
        return round(sum(vals) / len(vals) / 1000.0, 2) if vals else 0.0
    return {"p50": _avg("p50"), "p90": _avg("p90"), "p99": _avg("p99")}


def _estimate_cost(input_tokens: float, output_tokens: float,
                    model: str = _DEFAULT_MODEL) -> float:
    rates = MODEL_PRICING.get(model) or MODEL_PRICING[_DEFAULT_MODEL]
    return round(input_tokens / 1000.0 * rates["input_per_1k"]
                 + output_tokens / 1000.0 * rates["output_per_1k"], 2)


def _mock() -> dict[str, Any]:
    return {
        "invocations": 342, "input_tokens": 450000, "output_tokens": 120000,
        "estimated_cost": 3.15,
        "latency": {"p50": 1.2, "p90": 3.8, "p99": 8.3},
        "errors": 2, "error_rate": 0.006,
        "by_model": {
            "sonnet": {"invocations": 120, "avg_latency": 2.1},
            "haiku":  {"invocations": 222, "avg_latency": 0.8},
        },
        "mock": True,
    }


def get_bedrock_metrics(hours: int = 24) -> dict[str, Any]:
    """Aggregate Bedrock metrics over the last `hours`."""
    if MODE != "production":
        return _mock()
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    try:
        invocations = _sum_metric("Invocations", start, end)
        in_tokens = _sum_metric("InputTokenCount", start, end)
        out_tokens = _sum_metric("OutputTokenCount", start, end)
        client_err = _sum_metric("InvocationClientErrors", start, end)
        server_err = _sum_metric("InvocationServerErrors", start, end)
        latency = _latency_pct(start, end)
    except Exception as exc:
        logger.exception("bedrock_monitor aggregation failed")
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    errors = client_err + server_err
    err_rate = round(errors / invocations, 4) if invocations else 0.0
    return {
        "invocations": int(invocations),
        "input_tokens": int(in_tokens),
        "output_tokens": int(out_tokens),
        "estimated_cost": _estimate_cost(in_tokens, out_tokens),
        "latency": latency,
        "errors": int(errors),
        "error_rate": err_rate,
        "window_hours": hours,
        "checked_at": end.isoformat(),
    }


def ping() -> dict[str, Any]:
    """Lightweight Bedrock reachability probe (tiny prompt)."""
    if MODE != "production":
        return {"ok": True, "mock": True, "latency_ms": 10}
    try:
        import boto3
        import json as _json
        client = boto3.client("bedrock-runtime", region_name="us-east-1")
        started = time.time()
        resp = client.invoke_model(
            modelId="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            body=_json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "reply OK"}],
            }),
        )
        _ = resp["body"].read()
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def format_for_report(metrics: dict[str, Any] | None = None) -> str:
    m = metrics if metrics is not None else get_bedrock_metrics()
    if m.get("error"):
        return f"## Bedrock\n_Unavailable: {m['error']}_"
    lat = m.get("latency") or {}
    return "\n".join([
        "## Bedrock",
        f"- {m.get('invocations', 0)} calls / {m.get('window_hours', 24)}h · "
        f"est. ${m.get('estimated_cost', 0):.2f}",
        f"- Latency p50 {lat.get('p50', '?')}s · p90 {lat.get('p90', '?')}s · "
        f"p99 {lat.get('p99', '?')}s",
        f"- Errors: {m.get('errors', 0)} ({m.get('error_rate', 0) * 100:.1f}%)",
    ])


def journey_bedrock_health() -> dict[str, Any]:
    """Synthetic: Bedrock responds to a tiny prompt."""
    if MODE != "production":
        return {"name": "bedrock_health", "status": "skip",
                "error": "Requires production Bedrock access"}
    p = ping()
    if not p.get("ok"):
        return {"name": "bedrock_health", "status": "fail",
                "error": p.get("error", "ping failed")}
    return {"name": "bedrock_health", "status": "pass",
            "duration_ms": p.get("latency_ms", 0),
            "details": f"Haiku ping {p.get('latency_ms', 0)}ms"}

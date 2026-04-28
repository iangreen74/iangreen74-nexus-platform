"""Health signal evaluator for the Phase 0e.2 report engine.

For each ``HealthSignal`` declared on an OperatorFeature, fetch a
scalar value from the source picked by ``query_kind`` + ``query_spec``,
hand it to ``signal.status_for(value)`` (the 0e.1 threshold mapper),
and wrap the outcome in a ``SignalResult``.

Implemented kinds: ``CLOUDWATCH_METRIC``, ``CLOUDWATCH_LOG_COUNT``,
``POSTGRES_QUERY``, ``NEPTUNE_COUNT``, ``NEPTUNE_AGGREGATE``. Stub
(return None → ``UNKNOWN`` via ``status_for``): ``HTTP_HEALTH``. Stubs
are pluggable — fill in by adding to ``_VALUE_HANDLERS``.

The two Neptune handlers share a single shape (extract first scalar
from first row); the enum split between ``NEPTUNE_COUNT`` and
``NEPTUNE_AGGREGATE`` is operator-facing semantics — both dispatch to
``_eval_neptune_scalar``. Mirrors the canonical
``evidence_executor._exec_neptune_cypher`` pattern.

Per-signal exceptions are caught and converted to ``UNKNOWN`` with a
descriptive ``detail``; the loop continues for the rest of the
signals.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import boto3

from nexus.operator_features.report import SignalResult
from nexus.operator_features.schema import OperatorFeature
from nexus.operator_features.signals import (
    HealthSignal, SignalQueryKind, SignalStatus,
)

logger = logging.getLogger(__name__)

_AWS_REGION = "us-east-1"
_DEFAULT_WINDOW_SECONDS = 300
_DEFAULT_PERIOD = 60
_DEFAULT_STATISTIC = "Average"


def evaluate_health_signals(feature: OperatorFeature) -> list[SignalResult]:
    """Evaluate every HealthSignal on a feature."""
    return [_evaluate_one(sig) for sig in feature.health_signals]


def _evaluate_one(signal: HealthSignal) -> SignalResult:
    threshold = _format_threshold(signal)
    try:
        value = _query_value(signal)
    except Exception as exc:  # noqa: BLE001 — convert any failure to UNKNOWN
        logger.warning("signal eval failed: name=%s kind=%s err=%s",
                       signal.name, signal.query_kind, exc)
        return SignalResult(
            name=signal.name,
            status=SignalStatus.UNKNOWN,
            observed_value=None,
            threshold_summary=threshold,
            detail=f"query failed: {type(exc).__name__}: {exc}",
        )
    status = signal.status_for(value)
    return SignalResult(
        name=signal.name,
        status=status,
        observed_value=value,
        threshold_summary=threshold,
        detail=_format_detail(signal, value, status),
    )


def _query_value(signal: HealthSignal) -> float | None:
    """Dispatch to the appropriate per-kind handler. None → UNKNOWN."""
    handler = _VALUE_HANDLERS.get(signal.query_kind)
    if handler is None:
        return None
    return handler(signal.query_spec)


def _format_threshold(signal: HealthSignal) -> str:
    """Human-readable threshold summary, e.g. 'GREEN >= 95.0, AMBER >= 80.0, gte'."""
    op = "<=" if signal.comparison == "lte" else ">="
    return (
        f"GREEN {op} {signal.green_threshold} {signal.unit}, "
        f"AMBER {op} {signal.amber_threshold} {signal.unit}"
    )


def _format_detail(signal: HealthSignal, value: float | None,
                   status: SignalStatus) -> str:
    if value is None:
        return f"no value available ({status.value.upper()})"
    return f"{value} {signal.unit} ({status.value.upper()})"


# ---------------------------------------------------------------------------
# Per-kind value handlers
# ---------------------------------------------------------------------------

def _eval_cloudwatch_metric(spec: dict[str, Any]) -> float | None:
    """Spec: namespace, metric_name, dimensions, statistic, window_seconds, period."""
    cw = boto3.client("cloudwatch", region_name=_AWS_REGION)
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=spec.get("window_seconds",
                                             _DEFAULT_WINDOW_SECONDS))
    statistic = spec.get("statistic", _DEFAULT_STATISTIC)
    resp = cw.get_metric_statistics(
        Namespace=spec["namespace"],
        MetricName=spec["metric_name"],
        Dimensions=spec.get("dimensions", []),
        StartTime=start,
        EndTime=end,
        Period=spec.get("period", _DEFAULT_PERIOD),
        Statistics=[statistic],
    )
    points = resp.get("Datapoints") or []
    if not points:
        return None
    values = [p[statistic] for p in points if statistic in p]
    if not values:
        return None
    return sum(values) / len(values)


def _eval_cloudwatch_log_count(spec: dict[str, Any]) -> float:
    """Spec: log_group, filter_pattern, window_seconds. Returns event count."""
    logs = boto3.client("logs", region_name=_AWS_REGION)
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - spec.get("window_seconds",
                                 _DEFAULT_WINDOW_SECONDS) * 1000
    resp = logs.filter_log_events(
        logGroupName=spec["log_group"],
        startTime=start_ms,
        endTime=end_ms,
        filterPattern=spec.get("filter_pattern", ""),
        limit=spec.get("limit", 1000),
    )
    return float(len(resp.get("events") or []))


def _eval_postgres_query(spec: dict[str, Any]) -> float | None:
    """Spec: target ('v1'|'v2', default 'v1'), query (SQL → single scalar)."""
    from nexus.operator_features._pg import open_pg_connection
    target = spec.get("target", "v1")
    sql = spec["query"]
    with open_pg_connection(target) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def _eval_neptune_scalar(spec: dict[str, Any]) -> float | None:
    """Spec: cypher (Cypher → single scalar), (parameters).

    Used by both NEPTUNE_COUNT and NEPTUNE_AGGREGATE — both expect the
    first column of the first row to be a numeric scalar. Returns None
    when overwatch_graph.query yields no rows (typically a Neptune
    error or local mode — query is no-raise per its contract), when
    the row is shape-malformed, or when the scalar is None / non-
    numeric. status_for(None) maps to UNKNOWN.
    """
    from nexus import overwatch_graph
    cypher = spec["cypher"]
    parameters = spec.get("parameters") or {}
    rows = overwatch_graph.query(cypher, parameters)
    if not rows:
        return None
    first = rows[0]
    if not isinstance(first, dict) or not first:
        return None
    value = next(iter(first.values()))
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_VALUE_HANDLERS: dict[
    SignalQueryKind, Callable[[dict[str, Any]], float | None]
] = {
    SignalQueryKind.CLOUDWATCH_METRIC: _eval_cloudwatch_metric,
    SignalQueryKind.CLOUDWATCH_LOG_COUNT: _eval_cloudwatch_log_count,
    SignalQueryKind.POSTGRES_QUERY: _eval_postgres_query,
    SignalQueryKind.NEPTUNE_COUNT: _eval_neptune_scalar,
    SignalQueryKind.NEPTUNE_AGGREGATE: _eval_neptune_scalar,
    # HTTP_HEALTH still stubbed — falls through to None → UNKNOWN. Add
    # an entry here when its query semantics are designed.
}


__all__ = ["evaluate_health_signals"]

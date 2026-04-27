"""HealthSignal: thresholded measurements that contribute to OperatorFeature status.

Defines the operational-side signal primitives used by OperatorFeature
reports. A HealthSignal is a named, thresholded measurement (e.g.
"propose_object_success_rate_5m") that produces GREEN / AMBER / RED /
UNKNOWN per evaluation. The OperatorFeature aggregates many signals
into an overall status.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class SignalStatus(str, Enum):
    """Per-signal status. Aggregated up to overall OperatorFeature status."""
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNKNOWN = "unknown"


class SignalQueryKind(str, Enum):
    """How a HealthSignal queries its source."""
    CLOUDWATCH_METRIC = "cloudwatch_metric"
    CLOUDWATCH_LOG_COUNT = "cloudwatch_log_count"
    NEPTUNE_COUNT = "neptune_count"
    NEPTUNE_AGGREGATE = "neptune_aggregate"
    POSTGRES_QUERY = "postgres_query"
    HTTP_HEALTH = "http_health"


class HealthSignal(BaseModel):
    """A named, thresholded measurement contributing to OperatorFeature status.

    Threshold semantics depend on ``comparison``:
    - ``"gte"``: GREEN when value >= green_threshold,
                 AMBER when value >= amber_threshold,
                 else RED. Use for success rates, uptime, etc.
    - ``"lte"``: GREEN when value <= green_threshold,
                 AMBER when value <= amber_threshold,
                 else RED. Use for error rates, latencies, etc.
    """
    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    query_kind: SignalQueryKind
    query_spec: dict[str, Any]
    unit: str  # "percent", "seconds", "count", "rate", etc.
    green_threshold: float
    amber_threshold: float
    comparison: Literal["gte", "lte"]

    def status_for(self, value: float | None) -> SignalStatus:
        """Map a measured value to GREEN / AMBER / RED / UNKNOWN."""
        if value is None:
            return SignalStatus.UNKNOWN
        if self.comparison == "gte":
            if value >= self.green_threshold:
                return SignalStatus.GREEN
            if value >= self.amber_threshold:
                return SignalStatus.AMBER
            return SignalStatus.RED
        if value <= self.green_threshold:
            return SignalStatus.GREEN
        if value <= self.amber_threshold:
            return SignalStatus.AMBER
        return SignalStatus.RED

"""FeatureReport: structured output of the Phase 0e.2 report engine.

A FeatureReport is the deterministic snapshot of an OperatorFeature's
operational state at a point in time. It contains:

- ``dependencies`` — one DependencyStatus per OPERATOR_DEPENDS_ON edge,
  evaluated by querying the appropriate AWS API for the target node
  type (ECSService, RDSInstance, LambdaFunction, S3Bucket, …).
- ``health_signals`` — one SignalResult per HealthSignal on the feature,
  produced by querying ``query_kind`` + ``query_spec`` for a scalar
  value and feeding it through ``signal.status_for(value)``.
- ``evidence_queries`` — one QueryResult per EvidenceQuery on the
  feature, executed against the source picked by ``kind`` and tagged
  with the original ``section_kind`` so renderers (Echo tool 0e.3,
  Reports panel 0e.5) know whether to render as metric/table/list/text.
- ``overall_status`` — derived from the per-dep + per-signal statuses
  (RED if any RED, AMBER if any AMBER and none RED, UNKNOWN if any
  UNKNOWN and none RED/AMBER, else GREEN).

Status uses ``SignalStatus`` from ``nexus.operator_features.signals``
for naming alignment across the package. There is intentionally no
parallel ``HealthStatus`` / ``ReportStatus`` enum.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .signals import SignalStatus


class DependencyStatus(BaseModel):
    """One dependency's evaluated state."""
    model_config = ConfigDict(frozen=True)

    resource_type: str  # to_label from the OPERATOR_DEPENDS_ON edge
    resource_name: str  # to_id from the edge (cluster/service, db id, etc.)
    status: SignalStatus
    detail: str  # human-readable summary
    raw: dict[str, Any] = Field(default_factory=dict)  # API-returned fields


class SignalResult(BaseModel):
    """One health signal's evaluation result."""
    model_config = ConfigDict(frozen=True)

    name: str
    status: SignalStatus
    observed_value: float | None  # None when query failed / no datapoints
    threshold_summary: str  # e.g. "GREEN >= 95%, AMBER >= 80%, gte"
    detail: str  # human-readable observed-vs-expected line
    raw: dict[str, Any] = Field(default_factory=dict)


class QueryResult(BaseModel):
    """One evidence query's output, tagged with its renderer hint."""
    model_config = ConfigDict(frozen=True)

    name: str
    kind: str  # the EvidenceQueryKind that was dispatched
    section_kind: str  # "metric" | "table" | "list" | "text"
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    error: str | None = None  # populated when query failed or kind unimplemented


class FeatureReport(BaseModel):
    """Complete snapshot of an OperatorFeature's state at generation time."""
    model_config = ConfigDict(frozen=True)

    feature_id: str
    feature_name: str
    tenant_id: str
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    overall_status: SignalStatus
    falsifiability: str  # carried through from the OperatorFeature definition

    dependencies: list[DependencyStatus] = Field(default_factory=list)
    health_signals: list[SignalResult] = Field(default_factory=list)
    evidence_queries: list[QueryResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # engine annotations

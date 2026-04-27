"""Tests for nexus.operator_features.report Pydantic models."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timezone

import pytest  # noqa: E402

from nexus.operator_features.report import (  # noqa: E402
    DependencyStatus,
    FeatureReport,
    QueryResult,
    SignalResult,
)
from nexus.operator_features.signals import SignalStatus  # noqa: E402


def test_dependency_status_minimal_fields():
    d = DependencyStatus(
        resource_type="ECSService",
        resource_name="overwatch-platform/aria-console",
        status=SignalStatus.GREEN,
        detail="desired=2 running=2",
    )
    assert d.status == SignalStatus.GREEN
    assert d.raw == {}


def test_dependency_status_with_raw():
    d = DependencyStatus(
        resource_type="RDSInstance",
        resource_name="nexus-ontology-postgres",
        status=SignalStatus.GREEN,
        detail="status=available",
        raw={"engine": "postgres", "version": "16.13"},
    )
    assert d.raw["engine"] == "postgres"


def test_signal_result_with_value():
    s = SignalResult(
        name="success_rate_5m",
        status=SignalStatus.GREEN,
        observed_value=98.4,
        threshold_summary="GREEN >= 95.0, AMBER >= 80.0, gte",
        detail="98.4% over 5min (GREEN)",
    )
    assert s.observed_value == 98.4
    assert s.status == SignalStatus.GREEN


def test_signal_result_unknown_with_none_value():
    s = SignalResult(
        name="success_rate_5m",
        status=SignalStatus.UNKNOWN,
        observed_value=None,
        threshold_summary="GREEN >= 95.0, AMBER >= 80.0, gte",
        detail="no datapoints in window",
    )
    assert s.observed_value is None
    assert s.status == SignalStatus.UNKNOWN


def test_query_result_default_empty():
    q = QueryResult(name="recent rejects", kind="cloudwatch_logs",
                    section_kind="table")
    assert q.rows == []
    assert q.row_count == 0
    assert q.error is None


def test_query_result_with_rows():
    q = QueryResult(
        name="recent rejects",
        kind="cloudwatch_logs",
        section_kind="table",
        rows=[{"ts": "2026-04-27T18:00:00Z", "msg": "rejected"}],
        row_count=1,
    )
    assert q.row_count == 1
    assert q.rows[0]["msg"] == "rejected"


def test_query_result_error_path():
    q = QueryResult(
        name="cloudtrail recent",
        kind="cloudtrail_lookup",
        section_kind="table",
        error="kind not yet implemented",
    )
    assert q.rows == []
    assert q.error == "kind not yet implemented"


def test_feature_report_minimal_fields():
    r = FeatureReport(
        feature_id="ontology",
        feature_name="Ontology Capture",
        tenant_id="_fleet",
        overall_status=SignalStatus.GREEN,
        falsifiability="GREEN when ECS service running and accept rate > 95%",
    )
    assert r.dependencies == []
    assert r.health_signals == []
    assert r.evidence_queries == []
    assert r.notes == []
    # generated_at default-factory fires on construction
    assert isinstance(r.generated_at, datetime)
    assert r.generated_at.tzinfo is not None  # tz-aware


def test_feature_report_round_trip():
    """Pydantic model_dump → model_validate preserves all fields."""
    r = FeatureReport(
        feature_id="ontology",
        feature_name="Ontology Capture",
        tenant_id="_fleet",
        generated_at=datetime(2026, 4, 27, 18, 30, tzinfo=timezone.utc),
        overall_status=SignalStatus.AMBER,
        falsifiability="GREEN when …",
        dependencies=[DependencyStatus(
            resource_type="ECSService", resource_name="x",
            status=SignalStatus.AMBER, detail="degraded",
        )],
        health_signals=[SignalResult(
            name="rate", status=SignalStatus.GREEN,
            observed_value=99.0,
            threshold_summary="GREEN >= 95, gte",
            detail="99% (GREEN)",
        )],
        evidence_queries=[QueryResult(
            name="errors", kind="cloudwatch_logs", section_kind="list",
            rows=[{"x": 1}], row_count=1,
        )],
        notes=["test note"],
    )
    serialized = r.model_dump()
    restored = FeatureReport.model_validate(serialized)
    assert restored == r


def test_feature_report_overall_status_uses_signal_status_enum():
    """Enum-alignment regression: don't introduce a parallel HealthStatus."""
    r = FeatureReport(
        feature_id="x", feature_name="x", tenant_id="_fleet",
        overall_status=SignalStatus.RED, falsifiability="x",
    )
    assert r.overall_status == SignalStatus.RED
    # str-enum compatibility: serializes as its value string.
    assert r.model_dump()["overall_status"] == "red"


def test_feature_report_is_frozen():
    r = FeatureReport(
        feature_id="x", feature_name="x", tenant_id="_fleet",
        overall_status=SignalStatus.GREEN, falsifiability="x",
    )
    with pytest.raises(Exception):
        r.feature_id = "y"  # type: ignore[misc]

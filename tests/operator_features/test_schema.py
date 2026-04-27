"""Tests for nexus/operator_features schema (Pydantic models)."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.operator_features import (  # noqa: E402
    EvidenceQuery,
    EvidenceQueryKind,
    FeatureTier,
    HealthSignal,
    OperatorFeature,
    SignalQueryKind,
    SignalStatus,
)


# ---------------------------------------------------------------------------
# HealthSignal
# ---------------------------------------------------------------------------

def test_health_signal_status_for_gte():
    sig = HealthSignal(
        name="success_rate", description="x",
        query_kind=SignalQueryKind.CLOUDWATCH_LOG_COUNT,
        query_spec={}, unit="percent",
        green_threshold=95.0, amber_threshold=80.0, comparison="gte",
    )
    assert sig.status_for(98.0) == SignalStatus.GREEN
    assert sig.status_for(95.0) == SignalStatus.GREEN  # boundary inclusive
    assert sig.status_for(85.0) == SignalStatus.AMBER
    assert sig.status_for(80.0) == SignalStatus.AMBER  # boundary inclusive
    assert sig.status_for(50.0) == SignalStatus.RED
    assert sig.status_for(None) == SignalStatus.UNKNOWN


def test_health_signal_status_for_lte():
    sig = HealthSignal(
        name="error_rate", description="x",
        query_kind=SignalQueryKind.CLOUDWATCH_LOG_COUNT,
        query_spec={}, unit="percent",
        green_threshold=1.0, amber_threshold=5.0, comparison="lte",
    )
    assert sig.status_for(0.5) == SignalStatus.GREEN
    assert sig.status_for(1.0) == SignalStatus.GREEN  # boundary inclusive
    assert sig.status_for(3.0) == SignalStatus.AMBER
    assert sig.status_for(5.0) == SignalStatus.AMBER  # boundary inclusive
    assert sig.status_for(10.0) == SignalStatus.RED
    assert sig.status_for(None) == SignalStatus.UNKNOWN


def test_health_signal_is_frozen():
    sig = HealthSignal(
        name="x", description="x",
        query_kind=SignalQueryKind.HTTP_HEALTH,
        query_spec={}, unit="percent",
        green_threshold=1.0, amber_threshold=2.0, comparison="gte",
    )
    with pytest.raises(Exception):  # ValidationError under Pydantic v2 frozen
        sig.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EvidenceQuery
# ---------------------------------------------------------------------------

def test_evidence_query_defaults():
    eq = EvidenceQuery(
        name="recent failures",
        kind=EvidenceQueryKind.CLOUDWATCH_LOGS,
        spec={"log_group": "/ecs/forgescaler", "filter": "ERROR"},
        section_kind="table",
    )
    assert eq.accepts_tenant_id is False
    assert eq.max_results == 100
    assert eq.freshness_window_seconds is None


def test_evidence_query_tenant_opt_in():
    eq = EvidenceQuery(
        name="tenant-specific",
        kind=EvidenceQueryKind.NEPTUNE_CYPHER,
        spec={"cypher": "MATCH (t:Tenant {id: $tenant_id}) RETURN t"},
        section_kind="metric",
        accepts_tenant_id=True,
    )
    assert eq.accepts_tenant_id is True


# ---------------------------------------------------------------------------
# OperatorFeature
# ---------------------------------------------------------------------------

def test_operator_feature_round_trip():
    sig = HealthSignal(
        name="rate", description="x",
        query_kind=SignalQueryKind.CLOUDWATCH_LOG_COUNT,
        query_spec={}, unit="percent",
        green_threshold=95.0, amber_threshold=80.0, comparison="gte",
    )
    eq = EvidenceQuery(
        name="recent",
        kind=EvidenceQueryKind.CLOUDWATCH_LOGS,
        spec={}, section_kind="list",
    )
    feat = OperatorFeature(
        feature_id="test_feature",
        name="Test Feature",
        tier=FeatureTier.IMPORTANT,
        description="for tests only",
        health_signals=[sig],
        evidence_queries=[eq],
        falsifiability="GREEN when tests pass; RED when they don't",
    )
    serialized = feat.model_dump()
    restored = OperatorFeature.model_validate(serialized)
    assert restored == feat


def test_operator_feature_is_frozen():
    feat = OperatorFeature(
        feature_id="x", name="x", tier=FeatureTier.NICE_TO_HAVE,
        description="x", health_signals=[], evidence_queries=[],
        falsifiability="x",
    )
    with pytest.raises(Exception):
        feat.feature_id = "y"  # type: ignore[misc]


def test_feature_tier_int_values():
    """Tier enum is IntEnum so JSON dumps as 1/2/3, sortable."""
    assert int(FeatureTier.CRITICAL) == 1
    assert int(FeatureTier.IMPORTANT) == 2
    assert int(FeatureTier.NICE_TO_HAVE) == 3
    tiers = sorted([FeatureTier.NICE_TO_HAVE, FeatureTier.CRITICAL,
                    FeatureTier.IMPORTANT])
    assert tiers == [FeatureTier.CRITICAL, FeatureTier.IMPORTANT,
                     FeatureTier.NICE_TO_HAVE]

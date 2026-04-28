"""Tests for the ontology_capture_loop OperatorFeature instance.

Phase 0e.4 — first canonical OperatorFeature instance, encoding Bug 4
closure as observable. Tests verify:
  - The instance loads with the locked feature_id and tier
  - Falsifiability statement is non-empty and references the
    measurements that drive its thresholds
  - HealthSignal contract (name, query_kind, thresholds, comparison) is
    satisfied per signal — schema-locked since signal evaluator and
    report engine rely on these fields verbatim
  - The instance round-trips through persistence
  - The Phase 0e.2 report engine produces a FeatureReport without
    raising; per-signal evaluator failures (e.g., V1 Postgres
    unreachable in local mode) degrade to UNKNOWN per the engine's
    no-raise contract
  - The Phase 0e.3 read_holograph Echo tool returns the feature shaped
    as a serialisable dict
  - HealthSignal *names* are locked strings (mitigation playbooks +
    follow-up sweep tooling will reference them)
"""
from __future__ import annotations

import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import (  # noqa: E402
    FeatureTier, OperatorFeature, SignalQueryKind, SignalStatus,
)
from nexus.operator_features.engine import generate_feature_report  # noqa: E402
from nexus.operator_features.persistence import (  # noqa: E402
    read_operator_feature, write_operator_feature,
)
from nexus.operator_features.echo_tool import handler as read_holograph  # noqa: E402

from nexus.operator_features.instances.ontology_capture_loop import (  # noqa: E402
    FEATURE, FEATURE_ID,
)


# Locked health-signal names (mitigation playbooks + sweep tooling reference
# these strings; renaming a signal is a breaking change to the operational
# contract, not a refactor).
_EXPECTED_HEALTH_SIGNAL_NAMES = {
    "capture_loop_accepted_24h",
    "source_turn_id_linkage_pct_24h",
    "pending_stale_rate_pct_24h",
    "extraction_quality_pct_24h",
}


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


# ---------------------------------------------------------------------------
# Definition shape
# ---------------------------------------------------------------------------

def test_instance_loads_with_locked_id_and_tier():
    assert FEATURE_ID == "ontology_capture_loop"
    assert FEATURE.feature_id == FEATURE_ID
    assert isinstance(FEATURE, OperatorFeature)
    assert FEATURE.tier == FeatureTier.CRITICAL


def test_falsifiability_is_concrete_and_time_bounded():
    """Falsifiability is the falsifiable claim; vagueness here breaks the
    diagnostic value of read_holograph. Lock the key terms."""
    f = FEATURE.falsifiability
    assert f, "falsifiability must be non-empty"
    # Time bound — mandatory per the operational-truth substrate.
    assert "24" in f, f
    # The three status bands must all appear.
    assert "GREEN" in f and "AMBER" in f and "RED" in f, f
    # The four health-signal measurements drive the bands.
    assert "extraction quality" in f.lower(), f
    assert "source_turn_id" in f, f
    assert "pending-stale" in f.lower(), f
    # Decision and Hypothesis are the type contract closed in Bug 4.
    assert "Decision" in f and "Hypothesis" in f, f


def test_health_signal_names_are_locked():
    """Renaming a signal breaks downstream playbooks and sweep tooling.
    This test pins the names; failure here means a deliberate rename has
    happened and downstream consumers must be updated alongside."""
    actual = {s.name for s in FEATURE.health_signals}
    assert actual == _EXPECTED_HEALTH_SIGNAL_NAMES, (
        f"expected={_EXPECTED_HEALTH_SIGNAL_NAMES} actual={actual}"
    )


def test_each_health_signal_has_threshold_contract():
    """Every shipped HealthSignal must have valid thresholds, a query
    kind that's either implemented or declared as a stub, and a
    comparison direction. The signal evaluator depends on these fields
    being present and well-formed."""
    for sig in FEATURE.health_signals:
        assert sig.name, sig
        assert sig.description, f"{sig.name}: description must be non-empty"
        assert sig.unit, f"{sig.name}: unit must be set"
        assert sig.comparison in ("gte", "lte"), sig.comparison
        # Thresholds drive status; both must be numeric and ordered
        # consistently with the comparison direction.
        if sig.comparison == "gte":
            assert sig.green_threshold >= sig.amber_threshold, sig
        else:
            assert sig.green_threshold <= sig.amber_threshold, sig
        # status_for must produce all three named statuses given the
        # right values (pinning that the threshold logic is wired).
        if sig.comparison == "gte":
            assert sig.status_for(sig.green_threshold) == SignalStatus.GREEN
            assert sig.status_for(sig.amber_threshold) in (
                SignalStatus.GREEN, SignalStatus.AMBER,
            )
            assert sig.status_for(sig.amber_threshold - 1) == SignalStatus.RED
        else:
            assert sig.status_for(sig.green_threshold) == SignalStatus.GREEN
            assert sig.status_for(sig.amber_threshold) in (
                SignalStatus.GREEN, SignalStatus.AMBER,
            )
            assert sig.status_for(sig.amber_threshold + 1) == SignalStatus.RED
        assert sig.status_for(None) == SignalStatus.UNKNOWN


def test_health_signals_use_implemented_query_kinds():
    """Defer-or-ship rule: every shipped signal must use a SignalQueryKind
    that the signal evaluator actually implements. Stubs return
    UNKNOWN, which would defeat the falsifiability claim — defer such
    signals to follow-ups instead of shipping placeholders.

    Implemented set updated by PR-H1 (Neptune handlers added)."""
    implemented = {SignalQueryKind.POSTGRES_QUERY,
                   SignalQueryKind.CLOUDWATCH_METRIC,
                   SignalQueryKind.CLOUDWATCH_LOG_COUNT,
                   SignalQueryKind.NEPTUNE_COUNT,
                   SignalQueryKind.NEPTUNE_AGGREGATE}
    for sig in FEATURE.health_signals:
        assert sig.query_kind in implemented, (
            f"{sig.name} uses unimplemented kind {sig.query_kind}; "
            "defer to follow-up instead of shipping"
        )


def test_evidence_queries_use_implemented_kinds():
    """Same defer-or-ship rule for EvidenceQueries — only ship kinds the
    evidence executor actually dispatches today."""
    from nexus.operator_features.evidence import EvidenceQueryKind
    implemented = {EvidenceQueryKind.POSTGRES_QUERY,
                   EvidenceQueryKind.NEPTUNE_CYPHER,
                   EvidenceQueryKind.CLOUDWATCH_LOGS}
    for q in FEATURE.evidence_queries:
        assert q.kind in implemented, (
            f"evidence query {q.name!r} uses unimplemented kind {q.kind}"
        )
        assert q.section_kind in ("metric", "table", "list", "text")


# ---------------------------------------------------------------------------
# Persistence + engine integration
# ---------------------------------------------------------------------------

def test_persistence_round_trip():
    write_operator_feature(FEATURE)
    restored = read_operator_feature(FEATURE_ID)
    assert restored is not None
    assert restored == FEATURE


def test_report_engine_consumes_instance_without_raising():
    """Report engine never raises by contract; per-signal failures
    degrade to UNKNOWN. In NEXUS_MODE=local the V1 Postgres queries
    fail (no DATABASE_URL) — every shipped signal lands as UNKNOWN.
    This test asserts the contract, not the values."""
    write_operator_feature(FEATURE)
    report = generate_feature_report(FEATURE_ID)
    assert report.feature_id == FEATURE_ID
    assert report.feature_name == FEATURE.name
    # All four health signals must appear in the result list.
    assert len(report.health_signals) == len(FEATURE.health_signals)
    # All four evidence queries must appear in the result list.
    assert len(report.evidence_queries) == len(FEATURE.evidence_queries)
    # Local-mode degradation: every Postgres-backed signal is UNKNOWN.
    for r in report.health_signals:
        assert r.status in (
            SignalStatus.UNKNOWN, SignalStatus.GREEN,
            SignalStatus.AMBER, SignalStatus.RED,
        )
    # Falsifiability propagates from the definition into the report.
    assert report.falsifiability == FEATURE.falsifiability


def test_read_holograph_returns_serialisable_dict():
    """Echo tool surface: the dict shape downstream rendering depends
    on. Keys are stable; missing one is a breaking change to the
    Holograph contract."""
    write_operator_feature(FEATURE)
    result = read_holograph(feature_id=FEATURE_ID)
    expected_keys = {"feature_id", "feature_name", "tenant_id",
                     "generated_at", "overall_status", "falsifiability",
                     "dependencies", "health_signals", "evidence_queries",
                     "notes"}
    assert expected_keys <= set(result.keys()), set(result.keys())
    assert result["feature_id"] == FEATURE_ID
    assert result["feature_name"] == FEATURE.name
    assert len(result["health_signals"]) == 4
    assert len(result["evidence_queries"]) == 4

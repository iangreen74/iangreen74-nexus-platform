"""Tests for the deployment_capability OperatorFeature instance.

Phase 0e — second canonical OperatorFeature instance, encoding the
customer deployment capability's substrate-truth state. Mirrors the
ontology_capture_loop test contract:
  - Instance loads with locked feature_id and tier
  - Falsifiability statement is concrete, time-bounded, and references
    the measurements that drive its thresholds
  - HealthSignal contract (name, query_kind, thresholds, comparison) is
    satisfied per signal — schema-locked since signal evaluator and
    report engine rely on these fields verbatim
  - Defer-or-ship discipline: every shipped signal uses an implemented
    SignalQueryKind; deferred signals stay out (documented in the
    instance docstring instead)
  - The instance round-trips through persistence
  - The Phase 0e.2 report engine produces a FeatureReport without
    raising; per-signal evaluator failures degrade to UNKNOWN per the
    engine's no-raise contract
  - The Phase 0e.3 read_holograph Echo tool returns the feature shaped
    as a serialisable dict
  - HealthSignal *names* are locked strings (mitigation playbooks +
    follow-up sweep tooling will reference them)
  - Falsifiability length within 50 chars of the precedent (framework
    generality test — second instance shouldn't blow out the format)
"""
from __future__ import annotations

import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import (  # noqa: E402
    EvidenceQueryKind, FeatureTier, OperatorFeature, SignalQueryKind,
    SignalStatus,
)
from nexus.operator_features.engine import generate_feature_report  # noqa: E402
from nexus.operator_features.persistence import (  # noqa: E402
    read_operator_feature, write_operator_feature,
)
from nexus.operator_features.echo_tool import handler as read_holograph  # noqa: E402

from nexus.operator_features.instances.deployment_capability import (  # noqa: E402
    FEATURE, FEATURE_ID,
)
from nexus.operator_features.instances.ontology_capture_loop import (  # noqa: E402
    FEATURE as PRECEDENT_FEATURE,
)


# Locked health-signal names. Renaming a signal is a breaking change to
# the operational contract (mitigation playbooks + sweep tooling
# reference these strings) — not a refactor.
_EXPECTED_HEALTH_SIGNAL_NAMES = {
    "customer_tenant_successes_7d",
    "tenant_aws_role_arn_pct",
    "proven_stack_count",
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
    assert FEATURE_ID == "deployment_capability"
    assert FEATURE.feature_id == FEATURE_ID
    assert isinstance(FEATURE, OperatorFeature)
    assert FEATURE.tier == FeatureTier.CRITICAL


def test_falsifiability_is_concrete_and_time_bounded():
    """Falsifiability is the falsifiable claim; vagueness here breaks
    the diagnostic value of read_holograph. Lock the key terms."""
    f = FEATURE.falsifiability
    assert f, "falsifiability must be non-empty"
    assert len(f) > 200, f
    # Time bound — mandatory per the operational-truth substrate.
    assert "7-day" in f or "7d" in f, f
    # The three status bands must all appear.
    assert "GREEN" in f and "AMBER" in f and "RED" in f, f
    # The three shipped signals' measurements drive the bands.
    assert "customer-tenant" in f, f
    assert "aws_role_arn" in f, f
    assert "proven" in f.lower(), f
    # Signal references are concrete (Neptune labels) — operator can
    # cross-walk falsifiability text to actual graph queries.
    assert "DeploymentAttempt" in f, f
    assert "DeploymentFingerprint" in f, f


def test_falsifiability_length_close_to_precedent():
    """Framework generality test: a second instance authored against
    pre-existing requirements should produce a falsifiability statement
    of roughly the same shape as the first. >50-char drift suggests
    one of the two is over- or under-specifying."""
    delta = abs(len(FEATURE.falsifiability) - len(PRECEDENT_FEATURE.falsifiability))
    assert delta <= 50, (
        f"falsifiability length drift {delta} > 50 chars "
        f"(this={len(FEATURE.falsifiability)}, "
        f"precedent={len(PRECEDENT_FEATURE.falsifiability)})"
    )


def test_health_signal_names_are_locked():
    """Renaming a signal breaks downstream playbooks and sweep tooling.
    This test pins the names; failure here means a deliberate rename
    has happened and downstream consumers must be updated alongside."""
    actual = {s.name for s in FEATURE.health_signals}
    assert actual == _EXPECTED_HEALTH_SIGNAL_NAMES, (
        f"expected={_EXPECTED_HEALTH_SIGNAL_NAMES} actual={actual}"
    )


def test_three_signals_shipped_defer_or_ship_discipline():
    """Defer-or-ship: ship 3 signals (the Cypher-only ones); the four
    listed in the instance docstring stay deferred until their evidence
    kinds land. Adding a placeholder signal for a deferred kind would
    pollute the falsifiability claim with UNKNOWN values."""
    assert len(FEATURE.health_signals) == 3, len(FEATURE.health_signals)
    assert len(FEATURE.evidence_queries) == 4, len(FEATURE.evidence_queries)


def test_each_health_signal_has_threshold_contract():
    """Every shipped HealthSignal must have valid thresholds, a query
    kind that's implemented, and a comparison direction. The signal
    evaluator depends on these fields being present and well-formed."""
    for sig in FEATURE.health_signals:
        assert sig.name, sig
        assert sig.description, f"{sig.name}: description must be non-empty"
        assert sig.unit, f"{sig.name}: unit must be set"
        assert sig.comparison in ("gte", "lte"), sig.comparison
        # Thresholds drive status; both must be ordered consistently
        # with the comparison direction.
        if sig.comparison == "gte":
            assert sig.green_threshold >= sig.amber_threshold, sig
        else:
            assert sig.green_threshold <= sig.amber_threshold, sig
        # status_for produces all three named statuses given the
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


def test_signals_use_neptune_kinds_only():
    """Every shipped signal queries Neptune via NEPTUNE_COUNT or
    NEPTUNE_AGGREGATE — both implemented by PR-H1. No POSTGRES_QUERY,
    no CLOUDWATCH_*: deployment_capability lives entirely in the
    operational graph."""
    allowed = {SignalQueryKind.NEPTUNE_COUNT,
               SignalQueryKind.NEPTUNE_AGGREGATE}
    for sig in FEATURE.health_signals:
        assert sig.query_kind in allowed, (
            f"{sig.name} uses {sig.query_kind}; expected one of {allowed}"
        )
        # Cypher must be present — the Neptune handler reads spec['cypher'].
        assert "cypher" in sig.query_spec, sig.query_spec
        assert sig.query_spec["cypher"], sig.query_spec


def test_evidence_queries_use_neptune_cypher():
    """All four evidence queries are NEPTUNE_CYPHER tables. The
    deployment_capability evidence surface is graph-only by design;
    cross-source kinds (SFN, file/git, CloudTrail) are deferred per the
    instance docstring."""
    for q in FEATURE.evidence_queries:
        assert q.kind == EvidenceQueryKind.NEPTUNE_CYPHER, (
            f"evidence query {q.name!r} uses {q.kind}; expected NEPTUNE_CYPHER"
        )
        assert q.section_kind == "table", q.section_kind
        assert "cypher" in q.spec, q.spec


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
    degrade to UNKNOWN. In NEXUS_MODE=local the Neptune queries return
    no rows — every shipped signal lands as UNKNOWN. This test asserts
    the contract, not the values."""
    write_operator_feature(FEATURE)
    report = generate_feature_report(FEATURE_ID)
    assert report.feature_id == FEATURE_ID
    assert report.feature_name == FEATURE.name
    assert len(report.health_signals) == len(FEATURE.health_signals)
    assert len(report.evidence_queries) == len(FEATURE.evidence_queries)
    # Local-mode degradation: every Neptune-backed signal is UNKNOWN.
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
    assert len(result["health_signals"]) == 3
    assert len(result["evidence_queries"]) == 4

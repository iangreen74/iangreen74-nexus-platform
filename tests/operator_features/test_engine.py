"""Tests for nexus.operator_features.engine — top-level report assembly."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import engine  # noqa: E402
from nexus.operator_features.evidence import (  # noqa: E402
    EvidenceQuery, EvidenceQueryKind, FeatureTier,
)
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge, write_operator_feature,
)
from nexus.operator_features.report import (  # noqa: E402
    DependencyStatus, FeatureReport, QueryResult, SignalResult,
)
from nexus.operator_features.schema import OperatorFeature  # noqa: E402
from nexus.operator_features.signals import (  # noqa: E402
    HealthSignal, SignalQueryKind, SignalStatus,
)


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def _make_signal(name: str = "rate") -> HealthSignal:
    return HealthSignal(
        name=name, description="x",
        query_kind=SignalQueryKind.CLOUDWATCH_METRIC,
        query_spec={}, unit="percent",
        green_threshold=95.0, amber_threshold=80.0, comparison="gte",
    )


def _make_feature(
    feature_id: str = "x",
    signals: list[HealthSignal] | None = None,
    queries: list[EvidenceQuery] | None = None,
) -> OperatorFeature:
    return OperatorFeature(
        feature_id=feature_id, name=f"name-{feature_id}",
        tier=FeatureTier.NICE_TO_HAVE, description="x",
        health_signals=signals or [], evidence_queries=queries or [],
        falsifiability="GREEN when x; RED when y",
    )


# ---------------------------------------------------------------------------
# generate_feature_report — missing feature
# ---------------------------------------------------------------------------

def test_generate_report_missing_feature_returns_unknown():
    """The engine never raises; degenerate features get a stub report."""
    report = engine.generate_feature_report("does_not_exist")
    assert isinstance(report, FeatureReport)
    assert report.feature_id == "does_not_exist"
    assert report.feature_name == "<not found>"
    assert report.overall_status == SignalStatus.UNKNOWN
    assert report.dependencies == []
    assert report.health_signals == []
    assert report.evidence_queries == []
    assert len(report.notes) == 1
    assert "not found" in report.notes[0]


# ---------------------------------------------------------------------------
# _derive_overall_status — full truth table
# ---------------------------------------------------------------------------

def _dep(s: SignalStatus) -> DependencyStatus:
    return DependencyStatus(
        resource_type="X", resource_name="x", status=s, detail="x",
    )


def _sig(s: SignalStatus) -> SignalResult:
    return SignalResult(
        name="x", status=s, observed_value=None,
        threshold_summary="x", detail="x",
    )


def test_derive_overall_empty_inputs_is_unknown():
    assert engine._derive_overall_status([], []) == SignalStatus.UNKNOWN


def test_derive_overall_all_green_is_green():
    deps = [_dep(SignalStatus.GREEN), _dep(SignalStatus.GREEN)]
    sigs = [_sig(SignalStatus.GREEN)]
    assert engine._derive_overall_status(deps, sigs) == SignalStatus.GREEN


def test_derive_overall_one_red_is_red():
    """RED dominates everything — even amber/unknown/green present."""
    deps = [_dep(SignalStatus.GREEN), _dep(SignalStatus.AMBER)]
    sigs = [_sig(SignalStatus.RED), _sig(SignalStatus.UNKNOWN)]
    assert engine._derive_overall_status(deps, sigs) == SignalStatus.RED


def test_derive_overall_one_amber_no_red_is_amber():
    deps = [_dep(SignalStatus.GREEN)]
    sigs = [_sig(SignalStatus.AMBER), _sig(SignalStatus.GREEN)]
    assert engine._derive_overall_status(deps, sigs) == SignalStatus.AMBER


def test_derive_overall_one_unknown_no_red_or_amber_is_unknown():
    deps = [_dep(SignalStatus.GREEN), _dep(SignalStatus.UNKNOWN)]
    sigs = [_sig(SignalStatus.GREEN)]
    assert engine._derive_overall_status(deps, sigs) == SignalStatus.UNKNOWN


def test_derive_overall_amber_dominates_unknown():
    """AMBER takes precedence over UNKNOWN — degraded > can't-tell."""
    deps = [_dep(SignalStatus.AMBER), _dep(SignalStatus.UNKNOWN)]
    assert engine._derive_overall_status(deps, []) == SignalStatus.AMBER


# ---------------------------------------------------------------------------
# generate_feature_report — happy paths with mocked sub-evaluators
# ---------------------------------------------------------------------------

def test_generate_report_assembles_all_three_sections(monkeypatch):
    write_operator_feature(_make_feature(
        feature_id="ontology",
        signals=[_make_signal("rate")],
    ))

    fake_dep = DependencyStatus(
        resource_type="ECSService", resource_name="aria-console",
        status=SignalStatus.GREEN, detail="desired=2 running=2",
    )
    fake_sig = SignalResult(
        name="rate", status=SignalStatus.GREEN, observed_value=99.0,
        threshold_summary="GREEN >= 95.0 percent, AMBER >= 80.0 percent",
        detail="99.0 percent (GREEN)",
    )
    fake_q = QueryResult(
        name="recent rejects", kind="cloudwatch_logs",
        section_kind="table", rows=[{"x": 1}], row_count=1,
    )

    monkeypatch.setattr(engine, "walk_dependencies",
                        lambda fid, tenant_id="_fleet": [fake_dep])
    monkeypatch.setattr(engine, "evaluate_health_signals",
                        lambda f: [fake_sig])
    monkeypatch.setattr(engine, "execute_evidence_queries",
                        lambda f, tenant_id="_fleet": [fake_q])

    report = engine.generate_feature_report("ontology")
    assert report.feature_id == "ontology"
    assert report.feature_name == "name-ontology"
    assert report.overall_status == SignalStatus.GREEN
    assert report.dependencies == [fake_dep]
    assert report.health_signals == [fake_sig]
    assert report.evidence_queries == [fake_q]
    assert report.falsifiability == "GREEN when x; RED when y"


def test_generate_report_overall_red_when_dependency_red(monkeypatch):
    write_operator_feature(_make_feature("x"))
    monkeypatch.setattr(engine, "walk_dependencies",
                        lambda fid, tenant_id="_fleet":
                        [_dep(SignalStatus.RED)])
    monkeypatch.setattr(engine, "evaluate_health_signals", lambda f: [])
    monkeypatch.setattr(engine, "execute_evidence_queries",
                        lambda f, tenant_id="_fleet": [])
    report = engine.generate_feature_report("x")
    assert report.overall_status == SignalStatus.RED


def test_generate_report_passes_tenant_through(monkeypatch):
    """Engine must propagate tenant_id to the persistence/walker/executor."""
    write_operator_feature(_make_feature("x"), tenant_id="forge-A")

    captured: dict = {}

    def _fake_walk(fid, tenant_id="_fleet"):
        captured["walk_tenant"] = tenant_id
        return []

    def _fake_exec(feat, tenant_id="_fleet"):
        captured["exec_tenant"] = tenant_id
        return []

    monkeypatch.setattr(engine, "walk_dependencies", _fake_walk)
    monkeypatch.setattr(engine, "evaluate_health_signals", lambda f: [])
    monkeypatch.setattr(engine, "execute_evidence_queries", _fake_exec)

    report = engine.generate_feature_report("x", tenant_id="forge-A")
    assert report.tenant_id == "forge-A"
    assert captured["walk_tenant"] == "forge-A"
    assert captured["exec_tenant"] == "forge-A"


def test_generate_report_real_persistence_no_deps_no_signals():
    """End-to-end: write a bare feature, generate; no AWS calls happen
    because there are no deps or signals — overall_status is UNKNOWN
    (zero-input rule)."""
    write_operator_feature(_make_feature("bare"))
    report = engine.generate_feature_report("bare")
    assert report.feature_id == "bare"
    assert report.overall_status == SignalStatus.UNKNOWN
    assert report.dependencies == []
    assert report.health_signals == []


def test_generate_report_falsifiability_carried_through():
    feat = OperatorFeature(
        feature_id="fz", name="fz", tier=FeatureTier.IMPORTANT,
        description="x", health_signals=[], evidence_queries=[],
        falsifiability="UNIQUE_TEST_STRING_42",
    )
    write_operator_feature(feat)
    report = engine.generate_feature_report("fz")
    assert report.falsifiability == "UNIQUE_TEST_STRING_42"

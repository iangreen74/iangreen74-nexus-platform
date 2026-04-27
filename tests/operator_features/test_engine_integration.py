"""End-to-end integration test for the Phase 0e.2 report engine.

Writes a realistic-shaped OperatorFeature into the local-mode
operational graph via the 0e.1 persistence layer, mocks the AWS
clients the per-handler dispatchers reach for, and asserts
generate_feature_report produces a FeatureReport with the expected
structure.

This test is the closest the unit-suite can get to "real" behaviour
without VPC connectivity. It will be repurposed in Phase 0e.4 once
an actual Ontology OperatorFeature instance is curated.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import engine  # noqa: E402
from nexus.operator_features.evidence import (  # noqa: E402
    EvidenceQuery, EvidenceQueryKind, FeatureTier,
)
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge, write_operator_feature,
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


def _ontology_capture_feature() -> OperatorFeature:
    """Realistic-shaped feature: ECS + RDS deps, one signal, one query."""
    return OperatorFeature(
        feature_id="ontology_capture",
        name="Ontology Capture",
        tier=FeatureTier.CRITICAL,
        description=(
            "Founder Decisions / Features / Hypotheses captured by mechanism1 "
            "and persisted into V1 Postgres + Neptune."
        ),
        falsifiability=(
            "GREEN when classifier accept rate >= 95% and ECS+RDS healthy; "
            "AMBER on partial fleet or degraded RDS; RED on any service down "
            "or accept rate < 80%."
        ),
        health_signals=[HealthSignal(
            name="classifier_accept_success_rate_5m",
            description=(
                "Rolling 5-minute success rate of POST /api/classifier/<id>/accept"
            ),
            query_kind=SignalQueryKind.CLOUDWATCH_METRIC,
            query_spec={
                "namespace": "AriaPlatform/Classifier",
                "metric_name": "AcceptSuccessRate",
                "dimensions": [],
                "statistic": "Average",
                "window_seconds": 300,
            },
            unit="percent",
            green_threshold=95.0, amber_threshold=80.0, comparison="gte",
        )],
        evidence_queries=[EvidenceQuery(
            name="recent classifier rejects",
            kind=EvidenceQueryKind.CLOUDWATCH_LOGS,
            spec={
                "log_group": "/ecs/forgescaler",
                "filter_pattern": "rejected",
            },
            section_kind="table",
            max_results=10,
        )],
    )


def _setup_aws_mocks(monkeypatch, *, accept_rate: float):
    """Mock boto3.client to return clients that produce the desired scenario."""
    fake_ecs = MagicMock()
    fake_ecs.describe_services.return_value = {"services": [{
        "desiredCount": 2, "runningCount": 2, "status": "ACTIVE",
    }]}
    fake_rds = MagicMock()
    fake_rds.describe_db_instances.return_value = {"DBInstances": [{
        "DBInstanceStatus": "available",
        "Engine": "postgres",
        "AvailabilityZone": "us-east-1a",
    }]}
    fake_cw = MagicMock()
    fake_cw.get_metric_statistics.return_value = {"Datapoints": [
        {"Average": accept_rate}
    ]}
    fake_logs = MagicMock()
    fake_logs.filter_log_events.return_value = {"events": [{
        "timestamp": 1745778000000,
        "logStreamName": "console/aria-console/abc",
        "message": "candidate=06d8 rejected by founder",
    }]}

    by_service = {
        "ecs": fake_ecs, "rds": fake_rds,
        "cloudwatch": fake_cw, "logs": fake_logs,
    }

    def _factory(service: str, **_kwargs):
        return by_service[service]

    monkeypatch.setattr("boto3.client", _factory)


# ---------------------------------------------------------------------------
# Happy path — all green
# ---------------------------------------------------------------------------

def test_happy_path_all_green(monkeypatch):
    write_operator_feature(_ontology_capture_feature())
    add_dependency_edge("ontology_capture",
                        target_node_id="aria-console",
                        target_label="ECSService")
    add_dependency_edge("ontology_capture",
                        target_node_id="nexus-ontology-postgres",
                        target_label="RDSInstance")

    _setup_aws_mocks(monkeypatch, accept_rate=99.0)

    report = engine.generate_feature_report("ontology_capture")

    assert report.feature_id == "ontology_capture"
    assert report.feature_name == "Ontology Capture"
    assert report.overall_status == SignalStatus.GREEN

    deps_by_type = {d.resource_type: d for d in report.dependencies}
    assert set(deps_by_type) == {"ECSService", "RDSInstance"}
    assert all(d.status == SignalStatus.GREEN for d in report.dependencies)
    assert deps_by_type["ECSService"].resource_name == "aria-console"

    [signal] = report.health_signals
    assert signal.name == "classifier_accept_success_rate_5m"
    assert signal.observed_value == 99.0
    assert signal.status == SignalStatus.GREEN

    [evidence] = report.evidence_queries
    assert evidence.section_kind == "table"
    assert evidence.row_count == 1
    assert "rejected" in evidence.rows[0]["message"]
    assert evidence.error is None

    assert "classifier accept rate >= 95%" in report.falsifiability


# ---------------------------------------------------------------------------
# Degraded path — signal RED dominates overall
# ---------------------------------------------------------------------------

def test_degraded_signal_makes_overall_red(monkeypatch):
    """Signal value below amber threshold → RED → overall RED even
    though dependencies are GREEN. The whole point of falsifiability."""
    write_operator_feature(_ontology_capture_feature())
    add_dependency_edge("ontology_capture",
                        target_node_id="aria-console",
                        target_label="ECSService")

    _setup_aws_mocks(monkeypatch, accept_rate=50.0)  # < 80% amber → RED

    report = engine.generate_feature_report("ontology_capture")

    assert report.overall_status == SignalStatus.RED
    [signal] = report.health_signals
    assert signal.status == SignalStatus.RED
    assert signal.observed_value == 50.0
    # Dependencies are still individually GREEN — overall RED comes
    # from the signal, not the deps.
    assert all(d.status == SignalStatus.GREEN for d in report.dependencies)


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

def test_full_report_round_trips_through_json(monkeypatch):
    """Echo tool 0e.3 will dump-then-validate the report; verify that
    every populated field survives a model_dump → model_validate cycle."""
    write_operator_feature(_ontology_capture_feature())
    add_dependency_edge("ontology_capture",
                        target_node_id="aria-console",
                        target_label="ECSService")
    _setup_aws_mocks(monkeypatch, accept_rate=99.0)

    from nexus.operator_features.report import FeatureReport
    report = engine.generate_feature_report("ontology_capture")
    serialized = report.model_dump()
    restored = FeatureReport.model_validate(serialized)
    assert restored == report


# ---------------------------------------------------------------------------
# Failure resilience — one section breaking does not kill the report
# ---------------------------------------------------------------------------

def test_aws_failure_in_one_section_does_not_abort_report(monkeypatch):
    """If the metric API raises but logs work, the report still comes
    back with a degraded signal and a populated evidence section."""
    write_operator_feature(_ontology_capture_feature())
    add_dependency_edge("ontology_capture",
                        target_node_id="aria-console",
                        target_label="ECSService")

    fake_ecs = MagicMock()
    fake_ecs.describe_services.return_value = {"services": [{
        "desiredCount": 2, "runningCount": 2, "status": "ACTIVE",
    }]}
    fake_cw = MagicMock()
    fake_cw.get_metric_statistics.side_effect = RuntimeError(
        "simulated CloudWatch throttle"
    )
    fake_logs = MagicMock()
    fake_logs.filter_log_events.return_value = {"events": [{
        "timestamp": 1745778000000, "logStreamName": "x", "message": "ok",
    }]}

    by_service = {"ecs": fake_ecs, "cloudwatch": fake_cw, "logs": fake_logs}
    monkeypatch.setattr("boto3.client",
                        lambda s, **kw: by_service[s])

    report = engine.generate_feature_report("ontology_capture")

    # Engine returned a report despite the metric failure.
    assert report.feature_id == "ontology_capture"
    [signal] = report.health_signals
    assert signal.status == SignalStatus.UNKNOWN
    assert "RuntimeError" in signal.detail
    assert report.evidence_queries[0].row_count == 1
    # Overall: GREEN dep + UNKNOWN signal → UNKNOWN (no RED/AMBER).
    assert report.overall_status == SignalStatus.UNKNOWN

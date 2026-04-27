"""Tests for nexus.operator_features.dependencies — the dependency walker.

Covers per-label dispatch (ECSService, RDSInstance, LambdaFunction,
S3Bucket), unknown-label fallthrough, exception → UNKNOWN conversion,
and that the walk continues when a single check fails.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import dependencies  # noqa: E402
from nexus.operator_features.evidence import FeatureTier  # noqa: E402
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge,
    write_operator_feature,
)
from nexus.operator_features.report import DependencyStatus  # noqa: E402
from nexus.operator_features.schema import OperatorFeature  # noqa: E402
from nexus.operator_features.signals import SignalStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def _bare_feature(feature_id: str = "test_feature") -> OperatorFeature:
    return OperatorFeature(
        feature_id=feature_id, name=feature_id,
        tier=FeatureTier.NICE_TO_HAVE, description="x",
        health_signals=[], evidence_queries=[], falsifiability="x",
    )


# ---------------------------------------------------------------------------
# Top-level walk_dependencies
# ---------------------------------------------------------------------------

def test_walk_dependencies_empty_when_no_edges():
    write_operator_feature(_bare_feature("no_deps"))
    assert dependencies.walk_dependencies("no_deps") == []


def test_walk_dependencies_unknown_label_returns_unknown():
    write_operator_feature(_bare_feature("with_unknown"))
    add_dependency_edge("with_unknown", "weird:thing", "WeirdNode")
    results = dependencies.walk_dependencies("with_unknown")
    assert len(results) == 1
    assert results[0].resource_type == "WeirdNode"
    assert results[0].status == SignalStatus.UNKNOWN
    assert "no handler for resource_type='WeirdNode'" in results[0].detail


def test_walk_dependencies_one_failure_does_not_break_loop(monkeypatch):
    """A boto3 failure in one handler must not abort the walk."""
    write_operator_feature(_bare_feature("two_deps"))
    add_dependency_edge("two_deps", "ecs:thing-a", "ECSService")
    add_dependency_edge("two_deps", "weird:thing-b", "WeirdLabel")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated boto3 timeout")

    fake_ecs = MagicMock()
    fake_ecs.describe_services = _boom
    monkeypatch.setattr("boto3.client",
                        lambda *a, **kw: fake_ecs)

    results = dependencies.walk_dependencies("two_deps")
    assert len(results) == 2
    statuses = {r.resource_type: r.status for r in results}
    assert statuses["ECSService"] == SignalStatus.UNKNOWN
    assert statuses["WeirdLabel"] == SignalStatus.UNKNOWN


def test_walk_dependencies_missing_feature_returns_empty():
    """walk_dependencies on a non-existent feature returns []."""
    assert dependencies.walk_dependencies("does_not_exist") == []


# ---------------------------------------------------------------------------
# ECSService handler
# ---------------------------------------------------------------------------

def _make_ecs_response(desired=2, running=2, status="ACTIVE", missing=False):
    if missing:
        return {"services": []}
    return {"services": [{
        "desiredCount": desired, "runningCount": running, "status": status,
    }]}


@pytest.mark.parametrize("desired,running,svc_status,expected", [
    (2, 2, "ACTIVE", SignalStatus.GREEN),     # all healthy
    (3, 1, "ACTIVE", SignalStatus.AMBER),     # partial fleet
    (2, 0, "ACTIVE", SignalStatus.RED),       # none running, want some
    (0, 0, "ACTIVE", SignalStatus.RED),       # nothing desired (treated as RED — surface "off")
    (2, 2, "DRAINING", SignalStatus.AMBER),   # right counts, wrong status
])
def test_check_ecs_status_thresholds(desired, running, svc_status, expected):
    fake = MagicMock()
    fake.describe_services.return_value = _make_ecs_response(
        desired=desired, running=running, status=svc_status,
    )
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_ecs_service("aria-console")
    assert result.status == expected


def test_check_ecs_service_not_found_is_red():
    fake = MagicMock()
    fake.describe_services.return_value = _make_ecs_response(missing=True)
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_ecs_service("does-not-exist")
    assert result.status == SignalStatus.RED
    assert "not found" in result.detail


def test_check_ecs_parse_default_cluster():
    """Bare service id uses overwatch-platform as default cluster."""
    cluster, service = dependencies._parse_ecs_id("aria-console")
    assert cluster == "overwatch-platform"
    assert service == "aria-console"


def test_check_ecs_parse_explicit_cluster():
    cluster, service = dependencies._parse_ecs_id("custom-cluster/myapp")
    assert cluster == "custom-cluster"
    assert service == "myapp"


def test_check_ecs_passes_correct_cluster_to_boto3():
    fake = MagicMock()
    fake.describe_services.return_value = _make_ecs_response()
    with patch("boto3.client", return_value=fake):
        dependencies._check_ecs_service("custom/svc")
    fake.describe_services.assert_called_once_with(
        cluster="custom", services=["svc"],
    )


# ---------------------------------------------------------------------------
# RDSInstance handler
# ---------------------------------------------------------------------------

def test_check_rds_available_is_green():
    fake = MagicMock()
    fake.describe_db_instances.return_value = {"DBInstances": [{
        "DBInstanceStatus": "available",
        "Engine": "postgres",
        "AvailabilityZone": "us-east-1a",
    }]}
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_rds_instance("nexus-ontology-postgres")
    assert result.status == SignalStatus.GREEN
    assert result.raw["engine"] == "postgres"
    assert result.raw["az"] == "us-east-1a"


def test_check_rds_other_status_is_amber():
    fake = MagicMock()
    fake.describe_db_instances.return_value = {"DBInstances": [{
        "DBInstanceStatus": "modifying", "Engine": "postgres",
    }]}
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_rds_instance("nexus-ontology-postgres")
    assert result.status == SignalStatus.AMBER
    assert result.detail == "status=modifying"


def test_check_rds_missing_is_red():
    fake = MagicMock()
    fake.describe_db_instances.return_value = {"DBInstances": []}
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_rds_instance("does-not-exist")
    assert result.status == SignalStatus.RED


# ---------------------------------------------------------------------------
# LambdaFunction handler
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [
    ("Active", SignalStatus.GREEN),
    ("Pending", SignalStatus.AMBER),
    ("Inactive", SignalStatus.AMBER),
    ("Failed", SignalStatus.RED),
])
def test_check_lambda_states(state, expected):
    fake = MagicMock()
    fake.get_function_configuration.return_value = {
        "State": state, "Runtime": "python3.10",
    }
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_lambda_function("my-fn")
    assert result.status == expected


# ---------------------------------------------------------------------------
# S3Bucket handler
# ---------------------------------------------------------------------------

def test_check_s3_head_succeeds_green():
    fake = MagicMock()
    fake.head_bucket.return_value = {}
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_s3_bucket("my-bucket")
    assert result.status == SignalStatus.GREEN
    assert result.detail == "reachable"


def test_check_s3_head_raises_unknown():
    """head_bucket raising any error → walk's wrapper catches → UNKNOWN.

    Verified through the public _check_one wrapper, since _check_s3_bucket
    itself does no error handling (per the design where _check_one owns
    the universal except wrapping).
    """
    fake = MagicMock()
    fake.head_bucket.side_effect = RuntimeError("404 NoSuchBucket")
    with patch("boto3.client", return_value=fake):
        result = dependencies._check_one("S3Bucket", "ghost-bucket")
    assert result.status == SignalStatus.UNKNOWN
    assert "RuntimeError" in result.detail


# ---------------------------------------------------------------------------
# End-to-end via local persistence
# ---------------------------------------------------------------------------

def test_walk_dependencies_against_real_persistence(monkeypatch):
    """Write a feature with a real edge, walk it, assert handler dispatch."""
    write_operator_feature(_bare_feature("e2e_walk"))
    add_dependency_edge("e2e_walk", "aria-console", "ECSService")

    fake = MagicMock()
    fake.describe_services.return_value = _make_ecs_response(
        desired=2, running=2, status="ACTIVE",
    )
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake)

    results = dependencies.walk_dependencies("e2e_walk")
    assert len(results) == 1
    dep = results[0]
    assert isinstance(dep, DependencyStatus)
    assert dep.resource_type == "ECSService"
    assert dep.resource_name == "aria-console"
    assert dep.status == SignalStatus.GREEN

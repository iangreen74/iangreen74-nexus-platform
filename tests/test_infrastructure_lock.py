"""
Tests for the infrastructure lockdown sensor.

In NEXUS_MODE=local everything reports as locked because the sensor
returns mock cluster/services/graph/cognito state. The structural tests
here lock the report shape so future code changes don't silently drop
fields the dashboard depends on.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.sensors import infrastructure_lock  # noqa: E402


def test_locks_dict_has_expected_keys():
    locks = infrastructure_lock.INFRASTRUCTURE_LOCKS
    for key in (
        "aws_region",
        "aws_account",
        "ecs_cluster",
        "neptune_graph",
        "forgewing_domain",
        "api_domain",
        "staging_domain",
        "overwatch_domain",
        "cognito_pool",
        "github_app_id",
        "ecs_services",
    ):
        assert key in locks, f"missing locked constant: {key}"


def test_check_locks_returns_full_report_in_local_mode():
    report = infrastructure_lock.check_locks()
    assert "all_locked" in report
    assert "violations" in report
    assert "checks" in report
    assert "expected" in report
    # In local mode every backend mock reports healthy.
    assert isinstance(report["violations"], list)


def test_check_locks_local_passes():
    """Local-mode mocks should produce a fully-locked report."""
    report = infrastructure_lock.check_locks()
    # Local DNS check actually hits real DNS, so it can fail without network.
    # We tolerate DNS-only violations in local-mode tests.
    non_dns = [v for v in report["violations"] if not v["lock"].endswith("_domain")]
    assert non_dns == [], f"unexpected non-DNS violations: {non_dns}"


def test_aws_account_is_pinned():
    """Locking is meaningless if the account itself can change."""
    assert infrastructure_lock.INFRASTRUCTURE_LOCKS["aws_account"] == "418295677815"
    assert infrastructure_lock.INFRASTRUCTURE_LOCKS["aws_region"] == "us-east-1"

"""Tests for CI results reader (S3-based CI awareness)."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.ci_reader import (  # noqa: E402
    get_ci_health_summary,
    get_deploy_outcome_summary,
    get_latest_ci_result,
    get_latest_deploy_outcome,
)


def test_local_mode_returns_none():
    """In local mode, S3 reads return None (no boto3 calls)."""
    assert get_latest_ci_result() is None
    assert get_latest_deploy_outcome() is None


def test_ci_health_summary_unavailable():
    """When S3 data is missing, summary reports unavailable."""
    summary = get_ci_health_summary()
    assert summary["source"] == "s3"
    assert summary["status"] == "unavailable"


def test_deploy_outcome_summary_unavailable():
    """When S3 data is missing, deploy summary reports unavailable."""
    summary = get_deploy_outcome_summary()
    assert summary["source"] == "s3"
    assert summary["status"] == "unavailable"


def test_ci_health_summary_with_data(monkeypatch):
    """When S3 data is available, summary is correctly formatted."""
    mock_data = {
        "status": "passed",
        "total_tests": 1469,
        "passed_tests": 1469,
        "failed_tests": [],
        "commit_sha": "abc123def",
        "commit_message": "Data isolation pass 3",
        "timestamp": "2026-04-11T10:00:00Z",
        "run_url": "https://github.com/iangreen74/aria-platform/actions/runs/123",
        "duration_seconds": 312,
    }
    monkeypatch.setattr(
        "nexus.ci_reader.get_latest_ci_result", lambda: mock_data
    )
    summary = get_ci_health_summary()
    assert summary["source"] == "s3"
    assert summary["status"] == "passed"
    assert summary["total_tests"] == 1469
    assert summary["failed_count"] == 0
    assert summary["commit_sha"] == "abc123def"
    assert summary["duration_seconds"] == 312


def test_ci_health_summary_with_failures(monkeypatch):
    """Failed tests are counted correctly."""
    mock_data = {
        "status": "failed",
        "total_tests": 100,
        "passed_tests": 97,
        "failed_tests": ["test_a", "test_b", "test_c"],
        "commit_sha": "bad456",
    }
    monkeypatch.setattr(
        "nexus.ci_reader.get_latest_ci_result", lambda: mock_data
    )
    summary = get_ci_health_summary()
    assert summary["failed_count"] == 3
    assert summary["status"] == "failed"


def test_deploy_outcome_summary_with_data(monkeypatch):
    """Deploy outcome summary formats correctly."""
    mock_data = {
        "status": "success",
        "service": "forgescaler",
        "commit_sha": "abc123",
        "commit_message": "feature: new thing",
        "timestamp": "2026-04-11T12:00:00Z",
        "environment": "production",
    }
    monkeypatch.setattr(
        "nexus.ci_reader.get_latest_deploy_outcome", lambda: mock_data
    )
    summary = get_deploy_outcome_summary()
    assert summary["source"] == "s3"
    assert summary["status"] == "success"
    assert summary["service"] == "forgescaler"
    assert summary["environment"] == "production"

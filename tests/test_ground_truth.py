"""Tests for ground truth sensor."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.sensors.ground_truth import (  # noqa: E402
    check_app_url,
    get_deploy_ground_truth,
    get_full_pr_count,
    get_full_task_count,
    get_tenant_ground_truth,
    get_velocity,
)


def test_app_url_local():
    result = check_app_url("test")
    assert result["status"] == "live"
    assert result["http_status"] == 200


def test_deploy_ground_truth_local():
    gt = get_deploy_ground_truth("test")
    # Local mode: app_url mock returns live
    assert gt["deploy_status"] == "live"


def test_full_pr_count_local():
    prs = get_full_pr_count("test")
    assert prs["total"] >= 0
    assert "merged" in prs
    assert "pending" in prs


def test_full_task_count_local():
    tasks = get_full_task_count("test")
    assert tasks["total"] >= 0
    assert "complete" in tasks


def test_velocity_local():
    vel = get_velocity("test")
    assert "avg_pr_cycle_minutes" in vel
    assert "completion_rate" in vel


def test_tenant_ground_truth_local():
    gt = get_tenant_ground_truth("test")
    assert "deploy" in gt
    assert "prs" in gt
    assert "tasks" in gt
    assert "velocity" in gt


def test_deploy_healthy_means_provisioned():
    """When ground truth says live, describe_tenant_infra should be provisioned."""
    from nexus.aws_client import describe_tenant_infra
    result = describe_tenant_infra("test-tenant")
    assert result.get("provisioned") is True
    assert result.get("healthy") is True


def test_diagnostic_report_has_ground_truth():
    """Ground truth section should appear in the diagnostic report."""
    from fastapi.testclient import TestClient
    from nexus.server import app
    client = TestClient(app)
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "GROUND TRUTH" in report

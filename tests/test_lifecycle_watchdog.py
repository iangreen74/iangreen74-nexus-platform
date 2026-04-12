"""Tests for lifecycle watchdog (Class 1)."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timedelta, timezone  # noqa: E402

from nexus.capabilities.lifecycle_watchdog import (  # noqa: E402
    check_lifecycle,
    format_for_report,
)


def _iso(delta_hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=delta_hours)).isoformat()


def test_healthy_tenant_no_findings():
    """Tenant progressing normally returns no findings."""
    data = {
        "context": {"mission_stage": "executing", "created_at": _iso(1),
                    "updated_at": _iso(0.1)},
        "pipeline": {"pr_count": 3, "last_pr_at": _iso(0.5)},
        "deployment": {"provisioned": True},
    }
    assert check_lifecycle("t1", data) == []


def test_signup_stalled():
    data = {
        "context": {"mission_stage": "awaiting_repo", "created_at": _iso(2)},
        "pipeline": {}, "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "signup_stalled" for f in findings)


def test_ingestion_stuck():
    data = {
        "context": {"mission_stage": "ingesting", "updated_at": _iso(3)},
        "pipeline": {}, "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "ingestion_stuck" for f in findings)
    # Verify structure
    stuck = next(f for f in findings if f["check"] == "ingestion_stuck")
    assert stuck["hours"] > 1
    assert stuck["tenant_id"] == "t1"


def test_brief_stuck():
    data = {
        "context": {"mission_stage": "brief_generating", "updated_at": _iso(3)},
        "pipeline": {}, "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "brief_stuck" for f in findings)


def test_approval_stalled():
    data = {
        "context": {"mission_stage": "awaiting_approval", "updated_at": _iso(48)},
        "pipeline": {}, "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "approval_stalled" for f in findings)


def test_no_prs_after_approval():
    data = {
        "context": {"mission_stage": "executing", "updated_at": _iso(5)},
        "pipeline": {"pr_count": 0},
        "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "no_prs_after_approval" for f in findings)


def test_no_prs_cleared_when_prs_exist():
    """PRs exist → no_prs_after_approval should NOT fire."""
    data = {
        "context": {"mission_stage": "executing", "updated_at": _iso(5)},
        "pipeline": {"pr_count": 3, "last_pr_at": _iso(1)},
        "deployment": {},
    }
    findings = check_lifecycle("t1", data)
    assert not any(f["check"] == "no_prs_after_approval" for f in findings)


def test_pr_review_stalled():
    data = {
        "context": {"mission_stage": "executing"},
        "pipeline": {"pr_count": 2, "last_pr_at": _iso(72)},
        "deployment": {"provisioned": True},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "pr_review_stalled" for f in findings)


def test_deploy_not_started():
    data = {
        "context": {"mission_stage": "executing", "updated_at": _iso(48)},
        "pipeline": {"pr_count": 3, "last_pr_at": _iso(1)},
        "deployment": {"provisioned": False},
    }
    findings = check_lifecycle("t1", data)
    assert any(f["check"] == "deploy_not_started" for f in findings)


def test_deploy_started_clears_check():
    """Deploy provisioned → deploy_not_started should NOT fire."""
    data = {
        "context": {"mission_stage": "executing", "updated_at": _iso(48)},
        "pipeline": {"pr_count": 3, "last_pr_at": _iso(1)},
        "deployment": {"provisioned": True},
    }
    findings = check_lifecycle("t1", data)
    assert not any(f["check"] == "deploy_not_started" for f in findings)


def test_invalid_input_returns_empty():
    assert check_lifecycle("", {}) == []
    assert check_lifecycle("t1", None) == []


def test_format_for_report_empty():
    text = format_for_report({})
    assert "progressing normally" in text


def test_format_for_report_with_findings():
    findings = {"t1": [{"check": "brief_stuck", "hours": 3.2,
                        "diagnosis": "stuck", "suggested": "retry"}]}
    text = format_for_report(findings)
    assert "LIFECYCLE WATCHDOG" in text
    assert "brief_stuck" in text
    assert "3.2h" in text

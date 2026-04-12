"""Tests for consistency auditor (Class 2)."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities.consistency_auditor import (  # noqa: E402
    audit_global,
    audit_tenant,
    format_for_report,
)


def test_clean_tenant_no_findings():
    """All data consistent → no findings."""
    data = {
        "context": {"mission_stage": "executing", "repo_url": "https://github.com/x/y"},
        "active_project": {"repo_url": "https://github.com/x/y"},
        "pipeline": {"pr_count": 5, "github_pr_count": 5, "repo_file_count": 50},
        "token": {"present": True},
    }
    assert audit_tenant("t1", data) == []


def test_repo_url_mismatch_detected():
    """Tenant.repo_url != active project's repo_url."""
    data = {
        "context": {"mission_stage": "executing", "repo_url": "https://github.com/old/repo"},
        "active_project": {"repo_url": "https://github.com/new/repo"},
        "pipeline": {},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert any(f["check"] == "repo_url_sync" for f in findings)


def test_repo_url_match_no_finding():
    data = {
        "context": {"mission_stage": "executing", "repo_url": "https://github.com/x/y"},
        "active_project": {"repo_url": "https://github.com/x/y"},
        "pipeline": {},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert not any(f["check"] == "repo_url_sync" for f in findings)


def test_active_project_missing():
    data = {
        "context": {"mission_stage": "executing"},
        "active_project": None,
        "pipeline": {},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert any(f["check"] == "active_project_exists" for f in findings)


def test_active_project_ok_when_early_stage():
    """Awaiting repo is fine — no project expected yet."""
    data = {
        "context": {"mission_stage": "awaiting_repo"},
        "active_project": None,
        "pipeline": {},
        "token": {"present": False},
    }
    findings = audit_tenant("t1", data)
    assert not any(f["check"] == "active_project_exists" for f in findings)


def test_ingest_stage_sync_drift():
    """50 files indexed but still in 'ingesting' stage."""
    data = {
        "context": {"mission_stage": "ingesting"},
        "pipeline": {"repo_file_count": 50},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert any(f["check"] == "ingest_stage_sync" for f in findings)


def test_ingest_stage_ok_when_no_files():
    data = {
        "context": {"mission_stage": "ingesting"},
        "pipeline": {"repo_file_count": 0},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert not any(f["check"] == "ingest_stage_sync" for f in findings)


def test_pr_merge_sync_divergence():
    data = {
        "context": {"mission_stage": "executing"},
        "pipeline": {"pr_count": 10, "github_pr_count": 5},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert any(f["check"] == "pr_merge_sync" for f in findings)


def test_pr_merge_sync_within_tolerance():
    """Diff of 1 is within tolerance."""
    data = {
        "context": {"mission_stage": "executing"},
        "pipeline": {"pr_count": 5, "github_pr_count": 6},
        "token": {"present": True},
    }
    findings = audit_tenant("t1", data)
    assert not any(f["check"] == "pr_merge_sync" for f in findings)


def test_cloud_connection_invalid():
    data = {
        "context": {"mission_stage": "executing"},
        "pipeline": {},
        "token": {"present": False},
    }
    findings = audit_tenant("t1", data)
    assert any(f["check"] == "cloud_connection_valid" for f in findings)


def test_cloud_connection_ok_pre_onboarding():
    """Empty token before onboarding is expected."""
    data = {
        "context": {"mission_stage": "awaiting_repo"},
        "pipeline": {},
        "token": {"present": False},
    }
    findings = audit_tenant("t1", data)
    assert not any(f["check"] == "cloud_connection_valid" for f in findings)


def test_invalid_input():
    assert audit_tenant("", {}) == []
    assert audit_tenant("t1", None) == []


def test_audit_global_local_mode():
    """Local mode returns empty list (no Neptune)."""
    assert audit_global() == []


def test_format_empty():
    assert "no drift" in format_for_report({}, [])


def test_format_with_findings():
    findings = {"t1": [{"check": "repo_url_sync", "issue": "mismatch",
                        "auto_fixed": True, "fix_detail": "fixed"}]}
    text = format_for_report(findings, [])
    assert "DATA CONSISTENCY" in text
    assert "repo_url_sync" in text
    assert "auto-fixed" in text

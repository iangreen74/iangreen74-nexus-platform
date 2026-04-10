"""
Capability Validator tests — verifies report shape, scoring, triage
integration, and mock data flow in NEXUS_MODE=local.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.config import BLAST_SAFE  # noqa: E402
from nexus.reasoning import triage  # noqa: E402
from nexus.sensors import capability_validator  # noqa: E402


def test_validate_single_tenant_returns_report():
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    assert report.tenant_id == "tenant-alpha"
    assert report.timestamp
    assert report.layers_checked > 0
    assert len(report.checks) > 0
    assert report.overall in ("fully_operational", "degraded", "blocked", "onboarding")


def test_report_has_all_check_fields():
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    for check in report.checks:
        assert check.layer, "check missing layer"
        assert check.check, "check missing check name"
        assert check.status in ("pass", "fail", "warn", "skip")
        assert isinstance(check.detail, str)
        assert isinstance(check.auto_healable, bool)


def test_report_counts_are_consistent():
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    counted_pass = sum(1 for c in report.checks if c.status == "pass")
    counted_fail = sum(1 for c in report.checks if c.status == "fail")
    counted_warn = sum(1 for c in report.checks if c.status == "warn")
    assert report.checks_passed == counted_pass
    assert report.checks_failed == counted_fail
    assert report.checks_warned == counted_warn


def test_validate_all_tenants_returns_list():
    reports = capability_validator.validate_all_tenants()
    assert len(reports) == 3  # local mode returns 3 mock tenants
    ids = {r.tenant_id for r in reports}
    assert ids == {"tenant-alpha", "tenant-beta", "tenant-ben"}


def test_capability_score_format():
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    score = capability_validator.capability_score(report)
    assert "checks passing" in score
    assert "/" in score


def test_to_dict_has_required_keys():
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    d = report.to_dict()
    for key in ("tenant_id", "timestamp", "layers_checked", "checks_passed",
                "checks_failed", "checks_warned", "overall", "checks",
                "blockers", "score"):
        assert key in d, f"missing key: {key}"


def test_blockers_populated_on_fail():
    """If a check fails, the blocker should appear in the blockers list."""
    report = capability_validator.validate_tenant_capabilities("tenant-alpha")
    if report.checks_failed > 0:
        assert len(report.blockers) > 0


def test_triage_fully_operational_is_noop():
    decision = triage.triage_capability_report({
        "tenant_id": "t1",
        "overall": "fully_operational",
    })
    assert decision.action == "noop"
    assert decision.auto_approved is True


def test_triage_onboarding_is_noop():
    decision = triage.triage_capability_report({
        "tenant_id": "t1",
        "overall": "onboarding",
    })
    assert decision.action == "noop"
    assert decision.auto_approved is True


def test_triage_blocked_triggers_validation():
    decision = triage.triage_capability_report({
        "tenant_id": "t1",
        "overall": "blocked",
        "blockers": ["onboarding/token_valid: Token empty"],
    })
    assert decision.action == "validate_tenant_onboarding"
    assert decision.blast_radius == BLAST_SAFE


def test_triage_degraded_monitors_or_checks():
    decision = triage.triage_capability_report({
        "tenant_id": "t1",
        "overall": "degraded",
    })
    assert decision.action in ("check_pipeline_health", "monitor")


def test_known_pattern_capability_blocked():
    """The tenant_capability_blocked pattern should match."""
    decision = triage.triage_event("")  # won't match
    # Direct pattern test
    event = {"type": "capability_report", "overall": "blocked"}
    pattern = triage._match_pattern(event)
    assert pattern is not None
    assert pattern["name"] == "tenant_capability_blocked"


def test_known_pattern_capability_degraded():
    event = {"type": "capability_report", "overall": "degraded"}
    pattern = triage._match_pattern(event)
    assert pattern is not None
    assert pattern["name"] == "tenant_capability_degraded"


def test_local_mode_mock_data_produces_valid_report():
    """In local mode, mock data should produce a reasonable report."""
    report = capability_validator.validate_tenant_capabilities("tenant-ben")
    # Local mode returns mock tasks + PRs + conversation, so most checks pass.
    # Context has no mission_stage in local mode, so overall may be "onboarding".
    assert report.checks_passed > 0
    assert report.overall in ("fully_operational", "degraded", "onboarding")

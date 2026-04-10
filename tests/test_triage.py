"""
Triage engine tests — verifies seeded known patterns and rollup logic.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.config import BLAST_MODERATE, BLAST_SAFE  # noqa: E402
from nexus.reasoning import triage  # noqa: E402


def test_healthy_tenant_is_noop():
    decision = triage.triage_tenant_health({"tenant_id": "t1", "overall_status": "healthy"})
    assert decision.action == "noop"
    assert decision.auto_approved is True


def test_critical_with_unhealthy_deployment_restarts():
    decision = triage.triage_tenant_health(
        {
            "tenant_id": "t2",
            "overall_status": "critical",
            "deployment": {"healthy": False},
        }
    )
    assert decision.action == "restart_tenant_service"
    # Moderate blast radius is not auto-approved under the safety rule.
    assert decision.auto_approved is False


def test_critical_with_healthy_deployment_escalates():
    decision = triage.triage_tenant_health(
        {
            "tenant_id": "t3",
            "overall_status": "critical",
            "deployment": {"healthy": True},
        }
    )
    assert decision.action == "escalate_to_operator"
    assert decision.blast_radius != BLAST_SAFE


def test_daemon_stale_pattern_is_auto_healed():
    decision = triage.triage_daemon_health(
        {"running": True, "stale": True, "healthy": False}
    )
    assert decision.action == "restart_daemon_service"
    assert decision.blast_radius == BLAST_SAFE
    assert decision.auto_approved is True


def test_ci_failing_retriggers():
    decision = triage.triage_ci_health(
        {
            "healthy": False,
            "failing_workflows": ["deploy.yml"],
            "green_rate_24h": 0.3,
        }
    )
    assert decision.action == "retrigger_ci"
    assert decision.metadata.get("failing_workflows") == ["deploy.yml"]


def test_event_github_permission_escalates():
    decision = triage.triage_event("GitHub permission denied on push")
    assert decision.action == "escalate_to_operator"
    assert decision.blast_radius == BLAST_MODERATE
    assert decision.auto_approved is False


def test_event_bedrock_parse_is_auto_healed():
    decision = triage.triage_event("Cannot parse Bedrock response body")
    assert decision.action == "retry_with_fence_stripping"
    assert decision.blast_radius == BLAST_SAFE
    assert decision.auto_approved is True


def test_unknown_event_escalates_with_low_confidence():
    decision = triage.triage_event("completely novel failure mode")
    assert decision.action == "escalate_to_operator"
    assert decision.confidence < 0.8


def test_should_auto_heal_requires_high_confidence_and_safe():
    safe_high = triage.TriageDecision(
        action="x", confidence=0.9, reasoning="", blast_radius=BLAST_SAFE
    )
    safe_low = triage.TriageDecision(
        action="x", confidence=0.5, reasoning="", blast_radius=BLAST_SAFE
    )
    mod_high = triage.TriageDecision(
        action="x", confidence=0.9, reasoning="", blast_radius=BLAST_MODERATE
    )
    assert triage.should_auto_heal(safe_high) is True
    assert triage.should_auto_heal(safe_low) is False
    assert triage.should_auto_heal(mod_high) is False

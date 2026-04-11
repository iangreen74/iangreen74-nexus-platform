"""Tests for diagnostic deploy heal chain."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import deploy_ops  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.reasoning.heal_chain import get_chain  # noqa: E402
from nexus.reasoning.triage import _match_pattern  # noqa: E402


def test_check_readiness_registered():
    cap = registry.get("check_deploy_readiness")
    assert cap.blast_radius == "safe"


def test_diagnose_and_fix_registered():
    cap = registry.get("diagnose_and_fix_deploy")
    assert cap.blast_radius == "moderate"


def test_readiness_local_returns_ready():
    result = deploy_ops.check_deploy_readiness(tenant_id="test")
    assert result.get("ready") is True
    assert result.get("blockers") == []


def test_diagnose_fix_local():
    result = deploy_ops.diagnose_and_fix_deploy(tenant_id="test")
    assert result.get("mock") is True


def test_deploy_stuck_chain_uses_diagnostic():
    chain = get_chain("tenant_deploy_stuck")
    assert chain is not None
    assert chain.steps[0].capability == "check_deploy_readiness"
    assert chain.steps[1].capability == "diagnose_and_fix_deploy"
    assert chain.steps[2].capability == "validate_tenant_onboarding"


def test_deploy_stuck_pattern_routes_to_diagnostic():
    match = _match_pattern({"type": "tenant_health", "deploy_stuck": True})
    assert match is not None
    assert match["action"] == "diagnose_and_fix_deploy"


def test_precondition_failed_pattern():
    match = _match_pattern({"type": "deploy_readiness", "user_action_count": 2})
    assert match is not None
    assert match["name"] == "tenant_deploy_precondition_failed"
    assert match["action"] == "escalate_to_operator"


def test_rate_limit_tracks():
    deploy_ops._attempt_times.clear()
    assert deploy_ops._check_rate("test-tenant") is True
    for _ in range(3):
        deploy_ops._record_attempt("test-tenant")
    assert deploy_ops._check_rate("test-tenant") is False
    deploy_ops._attempt_times.clear()


def test_no_retry_when_user_blocker():
    """Ensure the chain pattern leads to escalation, not retry,
    when user-action blockers exist."""
    match = _match_pattern({"type": "deploy_readiness", "user_action_count": 1})
    assert match["action"] == "escalate_to_operator"

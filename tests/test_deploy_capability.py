"""Tests for deploy diagnosis + auto-fix capabilities."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import tenant_ops  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.reasoning.heal_chain import get_chain  # noqa: E402
from nexus.reasoning.triage import _match_pattern  # noqa: E402


def test_diagnose_deploy_registered():
    cap = registry.get("diagnose_tenant_deploy")
    assert cap.blast_radius == "safe"


def test_retry_deploy_registered():
    cap = registry.get("retry_tenant_deploy")
    assert cap.blast_radius == "moderate"


def test_diagnose_deploy_local():
    result = tenant_ops.diagnose_tenant_deploy(tenant_id="tenant-alpha")
    assert result.get("mock") is True
    assert result["tenant_id"] == "tenant-alpha"


def test_retry_deploy_local():
    result = tenant_ops.retry_tenant_deploy(tenant_id="tenant-alpha")
    assert result.get("mock") is True


def test_deploy_stuck_chain_exists():
    chain = get_chain("tenant_deploy_stuck")
    assert chain is not None
    assert len(chain.steps) == 3
    assert chain.steps[0].capability == "diagnose_tenant_deploy"
    assert chain.steps[1].capability == "retry_tenant_deploy"
    assert chain.steps[2].capability == "validate_tenant_onboarding"


def test_deploy_stuck_pattern_matches():
    match = _match_pattern({"type": "tenant_health", "deploy_stuck": True})
    assert match is not None
    assert match["name"] == "tenant_deploy_stuck"
    assert match["action"] == "retry_tenant_deploy"


def test_deploy_not_stuck_no_match():
    match = _match_pattern({"type": "tenant_health", "deploy_stuck": False})
    # Should not match tenant_deploy_stuck
    assert match is None or match["name"] != "tenant_deploy_stuck"

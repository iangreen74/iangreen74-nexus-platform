"""Tests for project lifecycle monitoring."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import project_lifecycle  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.reasoning.triage import _match_pattern  # noqa: E402


def test_lifecycle_capability_registered():
    cap = registry.get("get_project_lifecycle")
    assert cap.blast_radius == "safe"


def test_lifecycle_local_returns_active_project():
    result = project_lifecycle.get_project_lifecycle(tenant_id="test")
    assert result.get("mock") is True
    assert result.get("active_project") is not None


def test_project_archived_pattern():
    match = _match_pattern({"type": "project_lifecycle", "event_type": "archived"})
    assert match is not None
    assert match["name"] == "tenant_project_archived"
    assert match["action"] == "noop"


def test_project_restart_pattern():
    match = _match_pattern({"type": "project_lifecycle", "event_type": "restart"})
    assert match is not None
    assert match["name"] == "tenant_project_restart"
    assert match["action"] == "validate_tenant_onboarding"


def test_no_active_project_pattern():
    match = _match_pattern({"type": "project_lifecycle", "no_active_project": True})
    assert match is not None
    assert match["name"] == "tenant_no_active_project"
    assert match["action"] == "monitor"


def test_pending_restart_stale_pattern():
    match = _match_pattern({"type": "project_lifecycle", "stale_restart": True})
    assert match is not None
    assert match["name"] == "tenant_pending_restart_stale"
    assert match["action"] == "escalate_to_operator"


def test_check_all_tenants_lifecycle_local():
    result = project_lifecycle.check_all_tenants_lifecycle()
    assert "tenants" in result
    assert "alerts" in result

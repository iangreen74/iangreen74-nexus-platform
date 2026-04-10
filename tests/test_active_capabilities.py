"""
Tests for the active capability modules:
- tenant_ops (6 capabilities)
- daemon_ops (3 capabilities)
- ci_ops (2 capabilities)
- forgewing_api (client)
- tenant_validator (proactive sensor)

All run in NEXUS_MODE=local — no real AWS/GitHub calls.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import ci_ops, daemon_ops, tenant_ops  # noqa: E402
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.sensors import tenant_validator  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def setup_function(_fn):
    overwatch_graph.reset_local_store()


# --- Registry has all expected capabilities ---
def test_all_new_capabilities_registered():
    names = {c.name for c in registry.list_all()}
    for expected in (
        "refresh_tenant_token",
        "validate_tenant_onboarding",
        "verify_write_access",
        "retrigger_ingestion",
        "validate_repo_indexing",
        "check_pipeline_health",
        "restart_daemon",
        "diagnose_daemon_timeout",
        "check_daemon_code_version",
        "get_failing_workflows",
        "retrigger_workflow",
    ):
        assert expected in names, f"missing capability: {expected}"


# --- tenant_ops ---
def test_refresh_tenant_token_local():
    result = tenant_ops.refresh_tenant_token(tenant_id="tenant-alpha")
    assert result["refreshed"] is True
    assert result.get("mock") is True


def test_validate_onboarding_local():
    result = tenant_ops.validate_tenant_onboarding(tenant_id="tenant-alpha")
    assert "checks" in result
    assert result["tenant_id"] == "tenant-alpha"


def test_verify_write_access_local():
    result = tenant_ops.verify_write_access(tenant_id="tenant-alpha")
    assert result["write_access"] is True


def test_pipeline_health_local():
    result = tenant_ops.check_pipeline_health(tenant_id="tenant-alpha")
    assert "task_count" in result
    assert "pr_count" in result
    assert "blockers" in result


def test_validate_repo_indexing_local():
    result = tenant_ops.validate_repo_indexing(tenant_id="tenant-alpha")
    assert "repo_file_count" in result


def test_retrigger_ingestion_requires_tenant_id():
    result = tenant_ops.retrigger_ingestion()
    assert result.get("error")


# --- daemon_ops ---
def test_restart_daemon_local():
    result = daemon_ops.restart_daemon()
    assert result.get("restarted") is True


def test_diagnose_daemon_timeout_local():
    result = daemon_ops.diagnose_daemon_timeout()
    assert "slowest_hooks" in result


def test_check_daemon_code_version_local():
    result = daemon_ops.check_daemon_code_version()
    assert result.get("up_to_date") is True


# --- ci_ops ---
def test_get_failing_workflows_local():
    result = ci_ops.get_failing_workflows()
    assert "failing" in result


def test_retrigger_workflow_requires_run_id():
    result = ci_ops.retrigger_workflow()
    assert result.get("error")


def test_retrigger_workflow_local():
    result = ci_ops.retrigger_workflow(run_id=123)
    assert result.get("rerun") is True


# --- tenant_validator ---
def test_validate_all_tenants_local():
    results = tenant_validator.validate_all_tenants()
    assert isinstance(results, dict)
    assert "tenant-alpha" in results
    # In local mode mock data: tokens are present, tasks exist
    # So we shouldn't see critical alerts for mock tenants
    for tid, alerts in results.items():
        assert isinstance(alerts, list)


def test_validate_tenant_endpoint():
    resp = client.get("/api/validate/tenants")
    assert resp.status_code == 200
    body = resp.json()
    assert "tenant_count" in body
    assert "total_alerts" in body


# --- triage patterns ---
def test_new_ben_patterns_exist():
    from nexus.reasoning.triage import KNOWN_PATTERNS

    names = {p["name"] for p in KNOWN_PATTERNS}
    for expected in (
        "tenant_no_prs_after_tasks",
        "missing_repo_files",
        "empty_tenant_token",
        "write_access_denied",
        "daemon_timeout_recurring",
    ):
        assert expected in names, f"missing pattern: {expected}"


def test_empty_token_pattern_matches():
    from nexus.reasoning.triage import _match_pattern

    match = _match_pattern({"type": "tenant_health", "token_empty": True})
    assert match is not None
    assert match["name"] == "empty_tenant_token"


def test_write_access_denied_pattern():
    from nexus.reasoning.triage import _match_pattern

    match = _match_pattern({"type": "tenant_health", "write_access": False})
    assert match is not None
    assert match["name"] == "write_access_denied"


# --- capability count growing ---
def test_capability_count_is_substantial():
    """Overwatch should have 10+ registered capabilities by now."""
    assert len(registry.list_all()) >= 10

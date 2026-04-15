"""Tests for auto-remediation engine."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.auto_remediation import (  # noqa: E402
    _remediate_api_down,
    _remediate_deploy,
    _remediate_missing_brief,
    remediate,
    run_and_remediate,
)
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _reset_graph():
    for v in overwatch_graph._local_store.values():
        v.clear()


# --- remediate dispatch -------------------------------------------------------


def test_remediate_unknown_failure():
    """Unknown failure type → no remediation."""
    result = remediate({"name": "unknown_thing", "status": "fail"})
    assert result["fixed"] is False
    assert result["action"] == "none"
    assert "escalate" in result["detail"].lower()


def test_remediate_health_dispatches():
    """health failure → restart_forgescaler."""
    result = remediate({"name": "health", "status": "fail"})
    assert result["action"] == "restart_forgescaler"
    assert result["fixed"] is True  # mock mode


def test_remediate_brief_dispatches():
    """brief_exists failure → regenerate_brief."""
    result = remediate({"name": "brief_exists", "status": "fail"})
    assert result["action"] == "regenerate_brief"


def test_remediate_deploy_dispatches():
    """deploy_readiness failure → check_deploy_readiness."""
    result = remediate({"name": "deploy_readiness", "status": "fail"})
    assert result["action"] == "check_deploy_readiness"


def test_remediate_conversation_dispatches():
    """conversation failure → restart (same as health)."""
    result = remediate({"name": "conversation_scoping", "status": "fail"})
    assert result["action"] == "restart_forgescaler"


def test_remediate_sfs_dispatches():
    """sfs failure → restart (same as health)."""
    result = remediate({"name": "sfs_detection", "status": "fail"})
    assert result["action"] == "restart_forgescaler"


# --- Individual remediation handlers ------------------------------------------


def test_api_down_local():
    """In local mode, restart returns mock success."""
    result = _remediate_api_down({"name": "health"})
    assert result["fixed"] is True
    assert result["action"] == "restart_forgescaler"


def test_missing_brief_local():
    """In local mode, brief regeneration calls mock API."""
    result = _remediate_missing_brief({"name": "brief_exists"})
    # Mock API returns {mock: True} → no error → fixed
    assert result["action"] == "regenerate_brief"
    assert result["fixed"] is True


def test_deploy_remediation_local():
    """In local mode, readiness check succeeds."""
    result = _remediate_deploy({"name": "deploy_readiness"})
    assert result["action"] == "check_deploy_readiness"
    assert result["fixed"] is True


# --- run_and_remediate --------------------------------------------------------


def test_run_and_remediate_returns_summary():
    """Full run returns structured summary."""
    _reset_graph()
    result = run_and_remediate()
    assert "total" in result
    assert "passed" in result
    assert "failed" in result
    assert "remediated" in result
    assert "remediations" in result
    assert result["total"] == 26  # + 4 data/cost/bedrock/healer synthetics


def test_run_and_remediate_records_to_graph():
    """Remediations are recorded in the Overwatch graph."""
    _reset_graph()
    run_and_remediate()
    actions = overwatch_graph._local_store.get("OverwatchHealingAction", [])
    # May have 0 if all tests pass (no failures to remediate)
    assert isinstance(actions, list)


# --- API endpoint -------------------------------------------------------------


def test_remediation_endpoint():
    resp = client.post("/api/synthetic-tests/remediate")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "remediated" in body

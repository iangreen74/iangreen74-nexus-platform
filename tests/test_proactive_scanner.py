"""Tests for proactive scanner — pre-incident detection."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.proactive_scanner import (  # noqa: E402
    get_all_suggestions_summary,
    get_suggestions,
    scan_all_tenants,
    scan_deploy_health,
    scan_repo_health,
    scan_tenant,
)
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _reset_graph():
    for v in overwatch_graph._local_store.values():
        v.clear()


# --- Scan Functions -----------------------------------------------------------


def test_scan_all_tenants_returns_dict():
    """scan_all_tenants returns a dict of tenant_id -> suggestions."""
    result = scan_all_tenants()
    assert isinstance(result, dict)


def test_scan_tenant_returns_list():
    """scan_tenant returns a list of suggestions."""
    result = scan_tenant("tenant-alpha")
    assert isinstance(result, list)


def test_scan_repo_health_local():
    """In local mode, repo scan returns empty (no Neptune)."""
    result = scan_repo_health("tenant-alpha")
    assert isinstance(result, list)
    assert len(result) == 0  # local mode skips Neptune queries


def test_scan_deploy_health_local():
    """In local mode, deploy scan returns empty."""
    result = scan_deploy_health("tenant-alpha")
    assert isinstance(result, list)
    assert len(result) == 0


# --- Suggestions retrieval ----------------------------------------------------


def test_get_suggestions_local():
    """In local mode, get_suggestions returns empty list."""
    result = get_suggestions("tenant-alpha")
    assert isinstance(result, list)
    assert len(result) == 0


def test_get_all_suggestions_summary_empty():
    """Summary returns zeros when no suggestions exist."""
    _reset_graph()
    result = get_all_suggestions_summary()
    assert result["total"] == 0
    assert result["tenants_with_suggestions"] == 0


def test_get_all_suggestions_summary_with_data():
    """Summary correctly aggregates stored suggestions."""
    _reset_graph()
    # Simulate stored suggestions
    overwatch_graph.record_event(
        event_type="proactive_suggestion",
        service="tenant-alpha",
        severity="warning",
        details={
            "category": "repo_health",
            "title": "No repo files",
            "description": "test",
            "severity": "warning",
            "surfaced": False,
        },
    )
    overwatch_graph.record_event(
        event_type="proactive_suggestion",
        service="tenant-beta",
        severity="info",
        details={
            "category": "deploy_health",
            "title": "Deploy stuck",
            "description": "test",
            "severity": "info",
            "surfaced": False,
        },
    )
    result = get_all_suggestions_summary()
    assert result["total"] == 2
    assert result["tenants_with_suggestions"] == 2
    assert result["by_category"]["repo_health"] == 1
    assert result["by_category"]["deploy_health"] == 1


# --- API Endpoints ------------------------------------------------------------


def test_proactive_suggestions_endpoint():
    resp = client.get("/api/proactive-suggestions")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body


def test_tenant_suggestions_endpoint():
    resp = client.get("/api/proactive-suggestions/tenant-alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == "tenant-alpha"
    assert "suggestions" in body


def test_trigger_scan_endpoint():
    resp = client.post("/api/proactive-scan")
    assert resp.status_code == 200
    body = resp.json()
    assert "scanned" in body
    assert "suggestions" in body

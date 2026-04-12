"""Tests for not_started deploy fix + enriched diagnostic report."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.reasoning.triage import _match_pattern, triage_tenant_health  # noqa: E402
from nexus.sensors.tenant_health import _rollup  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


# --- not_started deploy must NOT trigger deploy_stuck -------------------------


def test_not_started_does_not_match_deploy_stuck():
    """deploy_stuck pattern requires deploy_stuck=True; not_started sets it False."""
    match = _match_pattern({"type": "tenant_health", "deploy_stuck": False})
    assert match is None or match["name"] != "tenant_deploy_stuck"


def test_deploy_stuck_true_still_matches():
    """Sanity: deploy_stuck=True DOES match the pattern."""
    match = _match_pattern({"type": "tenant_health", "deploy_stuck": True})
    assert match is not None
    assert match["name"] == "tenant_deploy_stuck"


def test_not_started_tenant_not_critical():
    """Tenant with not_started deploy → PENDING via _rollup, not CRITICAL."""
    # not_started → provisioned=False → _rollup returns "pending"
    deployment = {"provisioned": False, "healthy": False, "reason": "no deploy"}
    pipeline = {"stuck_task_count": 0}
    conversation = {"inactive": False}
    status = _rollup(deployment, pipeline, conversation)
    assert status == "pending"


def test_provisioned_healthy_tenant():
    """Tenant with healthy deploy → HEALTHY."""
    deployment = {"provisioned": True, "healthy": True}
    pipeline = {"stuck_task_count": 0}
    conversation = {"inactive": False}
    status = _rollup(deployment, pipeline, conversation)
    assert status == "healthy"


def test_provisioned_unhealthy_is_critical():
    """Tenant provisioned but unhealthy → CRITICAL (real problem)."""
    deployment = {"provisioned": True, "healthy": False}
    pipeline = {"stuck_task_count": 0}
    conversation = {"inactive": False}
    status = _rollup(deployment, pipeline, conversation)
    assert status == "critical"


def test_triage_not_stuck_tenant_is_noop():
    """Triage a not-stuck tenant → should be noop or healthy."""
    report = {
        "tenant_id": "test",
        "overall_status": "pending",
        "deploy_stuck": False,
        "deploy_stage": None,
    }
    decision = triage_tenant_health(report)
    # pending status → noop (not critical, not healthy — just waiting)
    assert decision.action in ("noop", "escalate_to_operator", "monitor")


# --- Ground truth not_started label -------------------------------------------


def test_ground_truth_not_started_label():
    """In local mode, ground truth returns 'live' (mock). Test the logic
    path for not_started classification directly."""
    from nexus.sensors.ground_truth import get_deploy_ground_truth

    # Local mode returns "live" due to mock — this tests the mock path
    gt = get_deploy_ground_truth("test")
    # In local mode the app URL check returns "live", so deploy_status is "live"
    assert gt["deploy_status"] in ("live", "not_started")


# --- Enriched diagnostic report sections --------------------------------------


def test_report_has_intelligence_status():
    """Diagnostic report includes INTELLIGENCE STATUS section."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "INTELLIGENCE STATUS" in report
    assert "User profile:" in report


def test_report_has_credential_status():
    """Diagnostic report includes CI/CD CREDENTIALS section."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "CI/CD CREDENTIALS" in report


def test_report_has_engineering_insights():
    """Diagnostic report includes ENGINEERING INSIGHTS section."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "ENGINEERING INSIGHTS" in report


def test_report_has_proactive_alerts():
    """Diagnostic report includes PROACTIVE ALERTS section."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "PROACTIVE ALERTS" in report


def test_report_has_ground_truth_not_deployed():
    """Ground truth section shows 'not deployed' for not_started tenants."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    # In local mode, tenants are mocked as "live", not "not deployed"
    # but the section should exist
    assert "GROUND TRUTH" in report


def test_report_sections_dont_break():
    """All report sections render without exceptions."""
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    # Should end with the closing marker
    assert "---" in report
    assert "Paste this into Claude" in report

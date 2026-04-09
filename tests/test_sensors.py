"""
Sensor layer tests — everything runs in NEXUS_MODE=local, which
means no AWS or Neptune connectivity is required.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.sensors import ci_monitor, daemon_monitor, tenant_health  # noqa: E402


def test_check_all_tenants_returns_mock_set():
    reports = tenant_health.check_all_tenants()
    assert len(reports) == 3
    ids = {r["tenant_id"] for r in reports}
    assert ids == {"tenant-alpha", "tenant-beta", "tenant-ben"}


def test_tenant_report_has_required_sections():
    report = tenant_health.check_tenant("tenant-alpha")
    for key in ("deployment", "pipeline", "conversation", "overall_status", "checked_at"):
        assert key in report, f"missing {key}"
    assert report["overall_status"] in {"healthy", "degraded", "critical"}


def test_tenant_report_deployment_fields():
    report = tenant_health.check_tenant("tenant-alpha")
    deployment = report["deployment"]
    assert "stack" in deployment
    assert "services" in deployment
    assert "healthy" in deployment


def test_daemon_report_shape():
    report = daemon_monitor.check_daemon()
    for key in ("running", "stale", "last_cycle_at", "error_rate", "healthy"):
        assert key in report, f"missing {key}"
    # In local mode the mocked last cycle is 3 minutes ago, so not stale.
    assert report["running"] is True
    assert report["stale"] is False


def test_ci_report_shape():
    report = ci_monitor.check_ci()
    for key in ("last_run_status", "green_rate_24h", "failing_workflows", "healthy"):
        assert key in report
    assert report["healthy"] is True
    assert report["last_run_status"] == "success"


def test_tenant_reports_never_raise():
    # Even with a nonsense tenant_id, the sensor must return a report, not raise.
    report = tenant_health.check_tenant("does-not-exist")
    assert "overall_status" in report

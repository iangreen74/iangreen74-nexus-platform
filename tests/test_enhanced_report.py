"""Tests for the enhanced diagnostic report."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.server import app  # noqa: E402

client = TestClient(app)


def test_diagnostic_report_has_all_sections():
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "OVERWATCH DIAGNOSTIC" in report
    assert "TENANT" in report
    assert "HEAL CHAINS" in report
    assert "TRIAGE DECISIONS" in report
    assert "FORGEWING CAPABILITIES" in report or "not yet discovered" in report
    assert "INFRASTRUCTURE" in report
    assert "RECENT ACTIONS" in report or "LEARNED FAILURE PATTERNS" in report
    assert "GRAPH:" in report


def test_diagnostic_report_includes_tenant_detail():
    resp = client.get("/api/diagnostic-report")
    report = resp.json()["report"]
    assert "Pipeline:" in report
    assert "Tasks:" in report
    assert "Token:" in report


def test_tenant_report_endpoint():
    resp = client.get("/api/tenant-report/tenant-alpha")
    assert resp.status_code == 200
    body = resp.json()
    assert "tenant-alpha" in body["report"]
    assert "TENANT REPORT" in body["report"]
    assert "Mission stage" in body["report"]
    assert "Triage:" in body["report"]


def test_tenant_report_has_tasks_and_prs():
    resp = client.get("/api/tenant-report/tenant-alpha")
    report = resp.json()["report"]
    assert "TASKS" in report
    assert "PRS" in report
    assert "Token:" in report

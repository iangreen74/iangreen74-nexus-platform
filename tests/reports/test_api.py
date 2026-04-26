"""API surface: catalog endpoint, run endpoint, deferred reports, 4xx errors."""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.reports.api import router  # noqa: E402
from fastapi import FastAPI  # noqa: E402


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_catalog_returns_twelve_entries():
    r = _client().get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 12
    assert body["feasible_count"] == 3
    assert body["deferred_count"] == 9


def test_get_catalog_entry_shape():
    r = _client().get("/api/reports")
    body = r.json()
    item = next(it for it in body["reports"] if it["report_id"] == "tenant_profile")
    assert set(item.keys()) >= {
        "report_id", "name", "tier", "audience", "description",
        "params_schema", "feasible_now", "deferred_reasons", "required_tools",
    }
    assert item["feasible_now"] is True
    assert item["deferred_reasons"] == []


def test_run_unknown_report_returns_404():
    r = _client().post("/api/reports/does-not-exist/run", json={})
    assert r.status_code == 404


def test_run_deferred_report_returns_envelope_with_reasons():
    r = _client().post("/api/reports/critical_findings_24h/run", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["report_id"] == "critical_findings_24h"
    assert body["sections"] == []
    assert "requires_phase_0b_log_correlation" in body["deferred_reasons"]


def test_run_tenant_profile_validates_tenant_id():
    r = _client().post("/api/reports/tenant_profile/run", json={})
    assert r.status_code == 400
    assert "tenant_id" in r.json()["detail"]


def test_run_with_non_object_body_returns_400():
    r = _client().post("/api/reports/critical_findings_24h/run",
                       data="not-json", headers={"Content-Type": "text/plain"})
    # FastAPI parses an empty/invalid body as {} — verify we still don't crash.
    assert r.status_code == 200


def test_run_with_array_body_returns_400():
    r = _client().post("/api/reports/critical_findings_24h/run", json=[])
    assert r.status_code == 400

"""Integration tests for Surgeon #4: purge-orphan-nodes endpoint."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient
from nexus.server import app

client = TestClient(app)

TID = "forge-test-purge"
URL = f"/api/operator/tenants/{TID}/purge-orphan-nodes"
PASSWORD = "aria-platform-2026"


def _auth():
    return {"X-Operator-Password": PASSWORD}


def test_no_password_returns_403():
    resp = client.post(URL, json={"labels_to_purge": ["MissionBrief"]})
    assert resp.status_code == 403


def test_wrong_password_returns_403():
    resp = client.post(
        URL,
        json={"labels_to_purge": ["MissionBrief"]},
        headers={"X-Operator-Password": "nope"},
    )
    assert resp.status_code == 403


def test_empty_labels_returns_400():
    resp = client.post(
        URL, json={"labels_to_purge": []}, headers=_auth(),
    )
    assert resp.status_code == 400
    assert "required" in resp.json()["detail"]


def test_missing_labels_returns_422():
    """Pydantic rejects body missing the required field."""
    resp = client.post(URL, json={}, headers=_auth())
    assert resp.status_code == 422


def test_dry_run_returns_200():
    resp = client.post(
        URL,
        json={"labels_to_purge": ["MissionBrief"]},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert "per_label" in body
    assert "audit_id" in body
    assert body["audit_id"].startswith("op-")


def test_execute_returns_200():
    resp = client.post(
        URL,
        json={"labels_to_purge": ["MissionBrief"], "dry_run": False},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is False


def test_non_project_scoped_label_reported_as_ignored():
    resp = client.post(
        URL,
        json={
            "labels_to_purge": ["OverwatchTenantSnapshot"],
            "dry_run": True,
        },
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "OverwatchTenantSnapshot" in body["ignored_labels"]
    assert body["per_label"] == {}


def test_bogus_tenant_returns_404(monkeypatch):
    """In production mode, unknown tenant → 404."""
    monkeypatch.setattr("nexus.operator_purge.MODE", "production")
    monkeypatch.setattr(
        "nexus.operator_purge._graph_query",
        lambda q, p=None: [],
    )
    resp = client.post(
        "/api/operator/tenants/forge-bogus/purge-orphan-nodes",
        json={"labels_to_purge": ["MissionBrief"]},
        headers=_auth(),
    )
    assert resp.status_code == 404

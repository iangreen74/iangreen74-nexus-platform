"""Integration tests for Surgeon #2: repair-orphan-nodes endpoint."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient
from nexus.server import app

client = TestClient(app)

TID = "forge-test-repair"
URL = f"/api/operator/tenants/{TID}/repair-orphan-nodes"
PASSWORD = "aria-platform-2026"
BODY = {"target_project_id": "proj-test-123", "dry_run": True}


def test_no_password_returns_403():
    resp = client.post(URL, json=BODY)
    assert resp.status_code == 403


def test_wrong_password_returns_403():
    resp = client.post(URL, json=BODY,
                       headers={"X-Operator-Password": "wrong"})
    assert resp.status_code == 403


def test_dry_run_returns_200():
    resp = client.post(URL, json=BODY,
                       headers={"X-Operator-Password": PASSWORD})
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert "per_label" in body
    assert "audit_id" in body
    assert body["audit_id"].startswith("op-")


def test_execute_returns_200():
    resp = client.post(
        URL,
        json={"target_project_id": "proj-test-123", "dry_run": False},
        headers={"X-Operator-Password": PASSWORD},
    )
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is False


def test_ignored_labels_reported():
    resp = client.post(
        URL,
        json={
            "target_project_id": "proj-test-123",
            "labels_to_repair": ["OverwatchTenantSnapshot", "MissionBrief"],
            "dry_run": True,
        },
        headers={"X-Operator-Password": PASSWORD},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "OverwatchTenantSnapshot" in body["ignored_labels"]

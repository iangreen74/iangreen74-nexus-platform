"""
Tests for the /api/support/escalate endpoint.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def setup_function(_fn):
    overwatch_graph.reset_local_store()


def test_escalate_requires_tenant_id():
    resp = client.post("/api/support/escalate", json={"issue": "stuck"})
    assert resp.status_code == 400


def test_escalate_records_event_and_returns_status():
    resp = client.post(
        "/api/support/escalate",
        json={"tenant_id": "tenant-alpha", "issue": "stuck task", "source": "aria"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("auto_healed", "escalated")
    assert body["tenant_id"] == "tenant-alpha"
    # Event recorded in graph
    events = overwatch_graph.get_recent_events()
    assert any(e["event_type"] == "support_escalation" for e in events)


def test_escalations_history_endpoint():
    client.post(
        "/api/support/escalate",
        json={"tenant_id": "tenant-beta", "issue": "test", "source": "aria"},
    )
    resp = client.get("/api/support/escalations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 1
    # Local-mode lookup falls back to scanning the in-memory store
    assert any(
        (e.get("details") or {}).get("tenant_id") == "tenant-beta"
        if isinstance(e.get("details"), dict)
        else "tenant-beta" in str(e.get("details", ""))
        for e in body["escalations"]
    )


def test_locks_endpoint_returns_report():
    resp = client.get("/api/locks")
    assert resp.status_code == 200
    body = resp.json()
    assert "all_locked" in body
    assert "violations" in body
    assert "expected" in body


def test_preemptive_endpoint_returns_alerts():
    resp = client.get("/api/preemptive")
    assert resp.status_code == 200
    body = resp.json()
    assert "alerts" in body
    assert isinstance(body["alerts"], list)


def test_status_includes_infrastructure_and_preemptive():
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "infrastructure" in body
    assert "preemptive" in body
    assert "all_locked" in body["infrastructure"]

"""Tests for deploy decision engine + deploy outcome learning."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.deploy_decision import evaluate_deploy_request  # noqa: E402
from nexus.deploy_patterns import (  # noqa: E402
    get_deploy_failure_count,
    get_deploy_success_rate,
    record_deploy_outcome,
)
from nexus import overwatch_graph  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _reset_graph():
    """Clear local-mode graph for test isolation."""
    for v in overwatch_graph._local_store.values():
        v.clear()


# --- Deploy Decision Tests ---------------------------------------------------


def test_deploy_all_clear():
    """DEPLOY when platform healthy, no chains, no failures, low risk."""
    result = evaluate_deploy_request({
        "commit_sha": "abc123",
        "service": "forgescaler",
        "environment": "production",
        "risk_score": 0.1,
    })
    assert result["decision"] in ("DEPLOY", "CANARY")  # CANARY if off-hours
    assert result["commit_sha"] == "abc123"
    assert result["service"] == "forgescaler"
    assert isinstance(result["factors"], list)
    assert len(result["factors"]) > 0


def test_deploy_hold_on_high_risk():
    """HOLD when risk score is very high."""
    result = evaluate_deploy_request({
        "commit_sha": "def456",
        "service": "forgescaler",
        "risk_score": 0.9,
    })
    assert result["decision"] == "HOLD"
    assert "risk score" in result["reason"].lower()


def test_deploy_canary_on_moderate_risk():
    """CANARY when risk is moderate (0.4 < risk <= 0.7)."""
    result = evaluate_deploy_request({
        "commit_sha": "ghi789",
        "service": "forgescaler",
        "risk_score": 0.5,
    })
    assert result["decision"] == "CANARY"


def test_factors_always_present():
    """Every decision includes a factors list with named entries."""
    result = evaluate_deploy_request({
        "commit_sha": "abc",
        "service": "forgescaler",
    })
    names = {f["name"] for f in result["factors"]}
    assert "platform_health" in names
    assert "active_heal_chains" in names
    assert "risk_score" in names
    assert "off_hours" in names


def test_zero_risk_score_default():
    """Missing risk_score defaults to 0."""
    result = evaluate_deploy_request({
        "commit_sha": "abc",
        "service": "forgescaler",
    })
    risk_factor = next(f for f in result["factors"] if f["name"] == "risk_score")
    assert risk_factor["value"] == 0.0


# --- Deploy Outcome Learning Tests -------------------------------------------


def test_record_deploy_outcome_stores():
    _reset_graph()
    record_deploy_outcome({
        "commit_sha": "abc123",
        "service": "forgescaler",
        "status": "success",
        "environment": "production",
    })
    events = overwatch_graph.get_recent_events(limit=10)
    deploy_events = [e for e in events if e.get("event_type") == "deploy_outcome"]
    assert len(deploy_events) == 1
    import json
    details = deploy_events[0]["details"]
    if isinstance(details, str):
        details = json.loads(details)
    assert details["status"] == "success"


def test_record_deploy_failure():
    _reset_graph()
    record_deploy_outcome({
        "commit_sha": "fail1",
        "service": "forgescaler",
        "status": "failed",
    })
    record_deploy_outcome({
        "commit_sha": "ok1",
        "service": "forgescaler",
        "status": "success",
    })
    stats = get_deploy_success_rate(hours=1)
    assert stats["total"] == 2
    assert stats["failed"] == 1
    assert 0.4 < stats["rate"] < 0.6


def test_deploy_failure_count():
    _reset_graph()
    for i in range(4):
        record_deploy_outcome({
            "commit_sha": f"fail{i}",
            "service": "forgescaler",
            "status": "failed" if i < 3 else "success",
        })
    assert get_deploy_failure_count(hours=1) == 3


def test_deploy_success_rate_empty():
    _reset_graph()
    stats = get_deploy_success_rate(hours=1)
    assert stats["rate"] == 1.0
    assert stats["total"] == 0


def test_rollback_counts_as_failure():
    _reset_graph()
    record_deploy_outcome({
        "commit_sha": "rb1",
        "service": "forgescaler",
        "status": "rollback",
    })
    assert get_deploy_failure_count(hours=1) == 1
    stats = get_deploy_success_rate(hours=1)
    assert stats["failed"] == 1


def test_record_outcome_returns_id():
    _reset_graph()
    node_id = record_deploy_outcome({
        "commit_sha": "abc",
        "service": "svc",
        "status": "success",
    })
    assert isinstance(node_id, str)
    assert len(node_id) > 0


# --- API Endpoint Tests ------------------------------------------------------


def test_deploy_decision_endpoint():
    resp = client.post("/api/deploy-decision", json={
        "commit_sha": "abc123",
        "service": "forgescaler",
        "risk_score": 0.1,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] in ("DEPLOY", "HOLD", "CANARY")
    assert "factors" in body


def test_deploy_decision_missing_sha():
    resp = client.post("/api/deploy-decision", json={
        "service": "forgescaler",
    })
    assert resp.status_code == 400


def test_deploy_decision_missing_service():
    resp = client.post("/api/deploy-decision", json={
        "commit_sha": "abc123",
    })
    assert resp.status_code == 400


def test_deploy_outcome_endpoint():
    _reset_graph()
    resp = client.post("/api/deploy-outcome", json={
        "commit_sha": "abc123",
        "service": "forgescaler",
        "status": "success",
        "environment": "production",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["recorded"] is True
    assert "event_id" in body


def test_deploy_outcome_missing_status():
    resp = client.post("/api/deploy-outcome", json={
        "commit_sha": "abc123",
    })
    assert resp.status_code == 400


def test_ci_s3_endpoint():
    resp = client.get("/api/ci/s3")
    assert resp.status_code == 200
    body = resp.json()
    assert "ci" in body
    assert "last_deploy" in body


def test_deploy_patterns_endpoint():
    resp = client.get("/api/deploy-patterns")
    assert resp.status_code == 200
    body = resp.json()
    assert "success_rate_24h" in body
    assert "failures_6h" in body

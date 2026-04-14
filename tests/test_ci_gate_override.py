"""Tests for the CI gate override — storage + integration with evaluate_ci_gate."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import ci_gate_override as gate_override  # noqa: E402
from nexus.capabilities.ci_cd_gates import evaluate_ci_gate  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    gate_override.reset_local_override()
    yield
    gate_override.reset_local_override()


# --- Module-level behavior ---------------------------------------------------


def test_set_and_get_override():
    stored = gate_override.set_override(
        decision="DEPLOY", reason="CI recovering", duration_minutes=60)
    assert stored["decision"] == "DEPLOY"
    assert stored["duration_minutes"] == 60

    active = gate_override.get_active_override()
    assert active is not None
    assert active["decision"] == "DEPLOY"
    assert active["reason"] == "CI recovering"


def test_expired_override_returns_none():
    gate_override.set_override("DEPLOY", "x", duration_minutes=1)
    # Rewrite the slot with an expiry in the past.
    with gate_override._local_lock:
        gate_override._local_slot["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert gate_override.get_active_override() is None


def test_clear_override():
    gate_override.set_override("HOLD", "x", duration_minutes=30)
    assert gate_override.clear_override() is True
    assert gate_override.get_active_override() is None
    # Second clear is a no-op
    assert gate_override.clear_override() is False


def test_only_one_active_override_at_a_time():
    gate_override.set_override("HOLD", "first", duration_minutes=60)
    gate_override.set_override("DEPLOY", "second", duration_minutes=30)
    active = gate_override.get_active_override()
    assert active["decision"] == "DEPLOY"
    assert active["reason"] == "second"


def test_invalid_decision_rejected():
    with pytest.raises(ValueError):
        gate_override.set_override("MAYBE", "x", duration_minutes=10)


def test_empty_reason_rejected():
    with pytest.raises(ValueError):
        gate_override.set_override("DEPLOY", "  ", duration_minutes=10)


def test_negative_duration_rejected():
    with pytest.raises(ValueError):
        gate_override.set_override("DEPLOY", "x", duration_minutes=0)


def test_excessive_duration_rejected():
    with pytest.raises(ValueError):
        gate_override.set_override(
            "DEPLOY", "x", duration_minutes=gate_override.MAX_DURATION_MINUTES + 1)


# --- Integration with evaluate_ci_gate --------------------------------------


def test_override_deploys_past_blocker():
    """Even if the readiness engine says HOLD, an active override wins."""
    with patch(
        "nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
        return_value={"decision": "HOLD", "reason": "ci tests blocked",
                       "blockers": ["ci_tests"], "warnings": [], "factors": {}},
    ):
        gate_override.set_override("DEPLOY", "recover", duration_minutes=60)
        result = evaluate_ci_gate(commit_sha="abc123")
    assert result["decision"] == "DEPLOY"
    assert result["source"] == "manual_override"
    assert result["commit"] == "abc123"
    assert result["blockers"] == []


def test_override_can_force_hold():
    """Operator can also force HOLD when the engine would DEPLOY."""
    with patch(
        "nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
        return_value={"decision": "DEPLOY", "reason": "green",
                       "blockers": [], "warnings": [], "factors": {}},
    ):
        gate_override.set_override("HOLD", "freeze", duration_minutes=60)
        result = evaluate_ci_gate()
    assert result["decision"] == "HOLD"
    assert result["source"] == "manual_override"


def test_expired_override_falls_through_to_engine():
    gate_override.set_override("DEPLOY", "stale", duration_minutes=1)
    with gate_override._local_lock:
        gate_override._local_slot["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with patch(
        "nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
        return_value={"decision": "HOLD", "reason": "blocker",
                       "blockers": ["x"], "warnings": [], "factors": {}},
    ):
        result = evaluate_ci_gate()
    assert result["decision"] == "HOLD"
    assert result.get("source") == "readiness_engine"


def test_cleared_override_falls_through_to_engine():
    gate_override.set_override("DEPLOY", "done", duration_minutes=60)
    gate_override.clear_override()
    with patch(
        "nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
        return_value={"decision": "HOLD", "reason": "blocker",
                       "blockers": ["x"], "warnings": [], "factors": {}},
    ):
        result = evaluate_ci_gate()
    assert result["decision"] == "HOLD"
    assert result.get("source") == "readiness_engine"


# --- Route smoke tests ------------------------------------------------------


def test_route_post_sets_override():
    r = client.post("/api/ci-gate-override", json={
        "decision": "DEPLOY",
        "reason": "CI recovering from morning outage",
        "duration_minutes": 120,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "override_set"
    assert body["decision"] == "DEPLOY"
    assert body["duration_minutes"] == 120


def test_route_get_reflects_override():
    client.post("/api/ci-gate-override", json={
        "decision": "DEPLOY", "reason": "x", "duration_minutes": 60,
    })
    r = client.get("/api/ci-gate-override")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["decision"] == "DEPLOY"


def test_route_delete_clears_override():
    client.post("/api/ci-gate-override", json={
        "decision": "DEPLOY", "reason": "x", "duration_minutes": 60,
    })
    r = client.delete("/api/ci-gate-override")
    assert r.status_code == 200
    assert r.json()["status"] == "cleared"
    assert client.get("/api/ci-gate-override").json()["status"] == "no_override"


def test_route_post_rejects_bad_decision():
    r = client.post("/api/ci-gate-override", json={
        "decision": "MAYBE", "reason": "x", "duration_minutes": 10,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "error"

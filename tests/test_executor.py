"""
Tests for the autonomous execution engine.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities import alert, ci_ops, daemon_ops, ecs_ops, tenant_ops  # noqa: E402,F401
from nexus.capabilities.registry import registry  # noqa: E402
from nexus.reasoning.executor import (  # noqa: E402
    ExecutionResult,
    execute_decision,
    reset_cooldowns,
)
from nexus.reasoning.triage import TriageDecision  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    reset_cooldowns()
    overwatch_graph.reset_local_store()
    # Reset the global registry rate-limit counter so each test starts fresh.
    registry._call_times.clear()
    yield
    reset_cooldowns()
    overwatch_graph.reset_local_store()
    registry._call_times.clear()


def _decision(action, confidence=0.9, blast="safe", auto=True):
    return TriageDecision(
        action=action,
        confidence=confidence,
        reasoning="test",
        blast_radius=blast,
        auto_approved=auto,
        metadata={"diagnosis": "test diagnosis", "resolution": "test resolution"},
    )


# --- Skip actions ---
def test_noop_is_skipped():
    r = execute_decision(_decision("noop"), {})
    assert r.status == "skipped"
    assert r.reason == "noop"


def test_monitor_is_skipped():
    r = execute_decision(_decision("monitor"), {})
    assert r.status == "skipped"


def test_unknown_action_is_skipped():
    r = execute_decision(_decision("some_unknown_thing"), {})
    assert r.status == "skipped"
    assert "no capability" in r.reason


# --- Escalation ---
def test_escalate_action_fires_escalation():
    r = execute_decision(_decision("escalate_to_operator", blast="moderate"), {"source": "test"})
    assert r.status == "escalated"
    assert r.action_taken == "send_escalation"


def test_escalate_with_diagnosis_fires():
    r = execute_decision(_decision("escalate_with_diagnosis", blast="moderate"), {"source": "ci"})
    assert r.status == "escalated"


# --- Confidence gates ---
def test_low_confidence_safe_action_skipped():
    r = execute_decision(
        _decision("restart_daemon_service", confidence=0.5, blast="safe"),
        {"source": "daemon", "target": "aria-daemon"},
    )
    assert r.status == "skipped"
    assert "confidence" in r.reason


def test_low_confidence_moderate_action_escalated():
    r = execute_decision(
        _decision("retrigger_ingestion", confidence=0.7, blast="moderate"),
        {"source": "tenant:x", "target": "x", "tenant_id": "x"},
    )
    assert r.status == "escalated"


# --- Blast radius gates ---
def test_dangerous_always_escalates():
    r = execute_decision(
        _decision("restart_daemon_service", confidence=0.99, blast="dangerous"),
        {"source": "daemon", "target": "aria-daemon"},
    )
    assert r.status == "escalated"
    assert "dangerous" in (r.reason or "")


# --- Successful execution ---
def test_safe_high_confidence_executes():
    r = execute_decision(
        _decision("restart_daemon_service", confidence=0.9, blast="safe"),
        {"source": "daemon", "target": "aria-daemon"},
    )
    assert r.status == "executed"
    assert r.outcome == "success"
    assert r.action_taken == "restart_daemon"


def test_moderate_high_confidence_executes():
    r = execute_decision(
        _decision("retrigger_ingestion", confidence=0.95, blast="moderate"),
        {"source": "tenant:t", "target": "t", "tenant_id": "t"},
    )
    assert r.status == "executed"
    assert r.action_taken == "retrigger_ingestion"


def test_tenant_token_refresh_executes():
    r = execute_decision(
        _decision("refresh_tenant_token", confidence=0.95, blast="safe"),
        {"source": "tenant:t", "target": "t", "tenant_id": "t"},
    )
    assert r.status == "executed"
    assert r.action_taken == "refresh_tenant_token"


# --- Cooldown ---
def test_cooldown_prevents_duplicate_execution():
    ctx = {"source": "daemon", "target": "aria-daemon"}
    r1 = execute_decision(_decision("restart_daemon_service"), ctx)
    assert r1.status == "executed"
    r2 = execute_decision(_decision("restart_daemon_service"), ctx)
    assert r2.status == "skipped"
    assert "cooldown" in r2.reason


def test_different_targets_not_affected_by_cooldown():
    ctx_a = {"source": "tenant:a", "target": "a", "tenant_id": "a"}
    ctx_b = {"source": "tenant:b", "target": "b", "tenant_id": "b"}
    r1 = execute_decision(_decision("refresh_tenant_token"), ctx_a)
    r2 = execute_decision(_decision("refresh_tenant_token"), ctx_b)
    assert r1.status == "executed"
    assert r2.status == "executed"


# --- Graph recording ---
def test_execution_records_to_graph():
    execute_decision(
        _decision("restart_daemon_service"),
        {"source": "daemon", "target": "aria-daemon"},
    )
    events = overwatch_graph.get_recent_events()
    exec_events = [e for e in events if e.get("event_type") == "execution"]
    assert len(exec_events) >= 1


def test_auto_heal_success_records_event():
    execute_decision(
        _decision("restart_daemon_service"),
        {"source": "daemon", "target": "aria-daemon"},
    )
    events = overwatch_graph.get_recent_events()
    heal_events = [e for e in events if e.get("event_type") == "auto_heal_success"]
    assert len(heal_events) >= 1


# --- Dashboard integration ---
def test_status_includes_executions():
    from fastapi.testclient import TestClient
    from nexus.server import app

    reset_cooldowns()
    client = TestClient(app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "executions" in body
    assert "execution_stats" in body
    assert isinstance(body["executions"], list)
    assert "executed" in body["execution_stats"]
    assert "escalated" in body["execution_stats"]

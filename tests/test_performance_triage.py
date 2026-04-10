"""Tests for performance triage patterns."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.reasoning.triage import triage_performance_alert  # noqa: E402


def test_daemon_drift_triggers_diagnosis():
    decision = triage_performance_alert({
        "metric": "daemon_cycle_duration",
        "anomalous": True,
        "value": 95.0,
        "baseline_mean": 35.0,
    })
    assert decision.action == "diagnose_daemon_timeout"
    assert decision.confidence >= 0.8


def test_velocity_drop_triggers_validation():
    decision = triage_performance_alert({
        "metric": "task_velocity",
        "tasks_per_day": 0,
        "was_active": True,
    })
    assert decision.action == "validate_tenant_onboarding"


def test_context_decline_triggers_validation():
    decision = triage_performance_alert({
        "metric": "context_health",
        "active": 2,
    })
    assert decision.action == "validate_tenant_onboarding"


def test_unknown_metric_monitors():
    decision = triage_performance_alert({
        "metric": "unknown_metric",
        "anomalous": True,
    })
    assert decision.action == "monitor"


def test_pr_slowdown_triggers_investigation():
    decision = triage_performance_alert({
        "metric": "pr_generation_time",
        "trend": "degrading",
    })
    assert decision.action == "investigate_stuck_tasks"

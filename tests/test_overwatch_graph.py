"""
Tests for Overwatch's own graph (overwatch_graph.py).

All tests run in NEXUS_MODE=local against the in-memory store.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def test_record_event_creates_node():
    eid = overwatch_graph.record_event("deployment", "aria-console", {"rev": 58}, "info")
    assert eid
    events = overwatch_graph.get_recent_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "deployment"
    assert events[0]["service"] == "aria-console"
    assert events[0]["severity"] == "info"


def test_record_failure_pattern_merges_by_name():
    overwatch_graph.record_failure_pattern(
        "github_permission_denied",
        signature="403 on push",
        diagnosis="GitHub App not installed",
        resolution="install the App",
        blast_radius="moderate",
        confidence=0.95,
    )
    overwatch_graph.record_failure_pattern(
        "github_permission_denied",
        signature="ignored",
        diagnosis="ignored",
        resolution="ignored",
    )
    overwatch_graph.record_failure_pattern(
        "github_permission_denied",
        signature="ignored",
        diagnosis="ignored",
        resolution="ignored",
    )
    patterns = overwatch_graph.get_failure_patterns()
    assert len(patterns) == 1
    assert patterns[0]["occurrence_count"] == 3


def test_record_healing_action():
    aid = overwatch_graph.record_healing_action(
        action_type="restart_service",
        target="aria-daemon",
        blast_radius="moderate",
        trigger="daemon_stale",
        outcome="success",
        duration_ms=1234,
    )
    assert aid
    history = overwatch_graph.get_healing_history(hours=1)
    assert len(history) == 1
    assert history[0]["target"] == "aria-daemon"
    assert history[0]["outcome"] == "success"


def test_record_tenant_snapshot_serializes_complex_values():
    overwatch_graph.record_tenant_snapshot(
        "tenant-alpha",
        {
            "overall_status": "healthy",
            "deployment_status": "healthy",
            "stuck_task_count": 0,
            "extra_metadata": {"k": "v"},  # dict — must be JSON-serialized
        },
    )
    trend = overwatch_graph.get_tenant_trend("tenant-alpha", days=1)
    assert len(trend) == 1
    snap = trend[0]
    assert snap["tenant_id"] == "tenant-alpha"
    assert snap["overall_status"] == "healthy"
    # The dict was coerced to a JSON string at the boundary.
    assert isinstance(snap["extra_metadata"], str)


def test_record_investigation():
    iid = overwatch_graph.record_investigation(
        trigger_event="daemon_stale",
        hypotheses=[{"h": "stuck cycle"}, {"h": "Bedrock throttling"}],
        conclusion="Bedrock throttling",
        confidence=0.85,
        resolution="restart daemon",
        outcome="resolved",
        duration_ms=5000,
    )
    assert iid


def test_record_human_decision():
    overwatch_graph.record_human_decision(
        decision_type="deploy",
        context="hotfix for daemon stall",
        action_taken="forced ECS deployment",
        outcome="recovered",
        automatable=True,
    )
    # In local mode the decision lives in the store; just verify it was added.
    assert overwatch_graph.graph_stats()["OverwatchHumanDecision"] == 1


def test_graph_stats_returns_per_label_counts():
    overwatch_graph.record_event("e", "s")
    overwatch_graph.record_event("e", "s")
    overwatch_graph.record_failure_pattern("p", "sig", "diag", "res")
    stats = overwatch_graph.graph_stats()
    assert stats["OverwatchPlatformEvent"] == 2
    assert stats["OverwatchFailurePattern"] == 1


def test_get_failure_patterns_filters_by_confidence():
    overwatch_graph.record_failure_pattern("low", "s", "d", "r", confidence=0.2)
    overwatch_graph.record_failure_pattern("high", "s", "d", "r", confidence=0.95)
    high = overwatch_graph.get_failure_patterns(min_confidence=0.5)
    assert len(high) == 1
    assert high[0]["name"] == "high"


def test_triage_decision_recorded_to_graph():
    """Triage now writes to the graph as a side effect."""
    from nexus.reasoning import triage

    overwatch_graph.reset_local_store()
    triage.triage_event("Cannot parse Bedrock response body")
    events = overwatch_graph.get_recent_events()
    assert any(e["event_type"] == "triage_decision" for e in events)
    patterns = overwatch_graph.get_failure_patterns()
    # bedrock_json_parse is a known pattern → MERGE creates / increments it
    assert any(p["name"] == "bedrock_json_parse" for p in patterns)


def test_unknown_event_creates_low_confidence_pattern():
    from nexus.reasoning import triage

    overwatch_graph.reset_local_store()
    triage.triage_event("completely novel and unique failure mode " + "X" * 50)
    patterns = overwatch_graph.get_failure_patterns()
    unknowns = [p for p in patterns if p["name"].startswith("unknown_")]
    assert len(unknowns) == 1
    assert unknowns[0]["confidence"] == 0.1

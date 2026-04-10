"""
Tests for SRE metrics engine and incident lifecycle.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.sensors import sre_metrics  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


# --- Incident lifecycle ---
def test_open_incident_creates_node():
    iid = overwatch_graph.open_incident("daemon", "daemon_stale", root_cause="stale cycle")
    assert iid
    open_inc = overwatch_graph.get_open_incidents()
    assert len(open_inc) == 1
    assert open_inc[0]["source"] == "daemon"
    assert open_inc[0]["type"] == "daemon_stale"


def test_open_incident_deduplicates():
    id1 = overwatch_graph.open_incident("daemon", "daemon_stale")
    id2 = overwatch_graph.open_incident("daemon", "daemon_stale")
    assert id1 == id2
    assert len(overwatch_graph.get_open_incidents()) == 1


def test_acknowledge_sets_timestamp():
    overwatch_graph.open_incident("daemon", "daemon_stale")
    overwatch_graph.acknowledge_incident("daemon", "restart_daemon")
    inc = overwatch_graph.get_open_incidents()[0]
    assert inc.get("acknowledged_at") is not None


def test_resolve_closes_incident():
    overwatch_graph.open_incident("daemon", "daemon_stale")
    overwatch_graph.acknowledge_incident("daemon", "restart_daemon")
    resolved = overwatch_graph.resolve_incident("daemon", auto_healed=True)
    assert resolved is not None
    assert resolved.get("resolved_at") is not None
    assert resolved.get("auto_healed") is True
    assert resolved.get("duration_seconds") is not None
    assert overwatch_graph.get_open_incidents() == []


def test_resolve_nonexistent_returns_none():
    assert overwatch_graph.resolve_incident("nonexistent") is None


def test_separate_sources_independent():
    overwatch_graph.open_incident("daemon", "daemon_stale")
    overwatch_graph.open_incident("ci", "ci_failing")
    assert len(overwatch_graph.get_open_incidents()) == 2
    overwatch_graph.resolve_incident("daemon", auto_healed=True)
    assert len(overwatch_graph.get_open_incidents()) == 1
    assert overwatch_graph.get_open_incidents()[0]["source"] == "ci"


# --- SRE metrics ---
def test_sre_dashboard_returns_all_fields():
    dashboard = sre_metrics.get_sre_dashboard()
    for key in (
        "mttd_seconds", "mtta_seconds", "mttr_seconds", "mtbf_hours",
        "change_failure_rate", "availability_percent", "error_budget",
        "antifragile_score", "open_incidents", "resolved_24h",
        "patterns_learned", "patterns_total_occurrences",
    ):
        assert key in dashboard, f"missing {key}"


def test_availability_is_100_with_no_incidents():
    avail = sre_metrics.compute_availability(24)
    assert avail == 100.0


def test_error_budget_full_with_no_incidents():
    budget = sre_metrics.compute_error_budget(30)
    assert budget["consumed_minutes"] == 0.0
    assert budget["remaining_minutes"] > 40  # ~43.2 min for 99.9%


def test_antifragile_score_range():
    score = sre_metrics.compute_antifragile_score()
    assert 0 <= score <= 100


def test_mttr_with_resolved_incidents():
    overwatch_graph.open_incident("test", "test_type")
    overwatch_graph.acknowledge_incident("test", "test_action")
    overwatch_graph.resolve_incident("test", auto_healed=True)
    mttr = sre_metrics.compute_mttr(24)
    assert mttr is not None
    assert mttr >= 0


def test_mtta_with_acknowledged_incidents():
    overwatch_graph.open_incident("test", "test_type")
    overwatch_graph.acknowledge_incident("test", "test_action")
    overwatch_graph.resolve_incident("test")
    mtta = sre_metrics.compute_mtta(24)
    assert mtta is not None
    assert mtta >= 0


def test_sre_api_endpoint():
    from fastapi.testclient import TestClient
    from nexus.server import app

    client = TestClient(app)
    resp = client.get("/api/sre")
    assert resp.status_code == 200
    body = resp.json()
    assert "antifragile_score" in body
    assert "error_budget" in body


def test_sre_incidents_endpoint():
    from fastapi.testclient import TestClient
    from nexus.server import app

    overwatch_graph.open_incident("test", "test_type")
    client = TestClient(app)
    resp = client.get("/api/sre/incidents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open_count"] >= 1


def test_trend_function():
    assert sre_metrics._trend(10, 20, lower_is_better=True) == "improving"
    assert sre_metrics._trend(20, 10, lower_is_better=True) == "degrading"
    assert sre_metrics._trend(10, 10.01, lower_is_better=True) == "stable"
    assert sre_metrics._trend(None, 10) == "unknown"

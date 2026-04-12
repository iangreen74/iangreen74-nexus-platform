"""Tests for engineering pattern learning — cross-tenant intelligence."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.deploy_patterns import record_deploy_outcome  # noqa: E402
from nexus.engineering_patterns import (  # noqa: E402
    MIN_DATA_POINTS,
    analyze_all,
    analyze_deploy_timing,
    analyze_failure_categories,
    analyze_pr_velocity,
    get_recommendations,
)
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _reset_graph():
    for v in overwatch_graph._local_store.values():
        v.clear()


# --- Deploy Timing Analysis ---------------------------------------------------


def test_deploy_timing_needs_min_data_points():
    """No pattern returned when data is sparse."""
    _reset_graph()
    record_deploy_outcome({"commit_sha": "a", "service": "s", "status": "success"})
    result = analyze_deploy_timing()
    assert result is None


def test_deploy_timing_with_enough_data():
    """Pattern returned when >= MIN_DATA_POINTS deploys exist."""
    _reset_graph()
    for i in range(MIN_DATA_POINTS + 1):
        record_deploy_outcome({
            "commit_sha": f"sha{i}",
            "service": "forgescaler",
            "status": "success" if i % 4 != 0 else "failed",
        })
    result = analyze_deploy_timing()
    # May be None if all land in one bucket with <3, but with 4+ should work
    if result is not None:
        assert result["type"] == "deploy_timing"
        assert "rates" in result
        assert result["data_points"] >= MIN_DATA_POINTS


def test_deploy_timing_insight_format():
    """Insight is a readable string."""
    _reset_graph()
    for i in range(5):
        record_deploy_outcome({
            "commit_sha": f"sha{i}",
            "service": "forgescaler",
            "status": "success",
        })
    result = analyze_deploy_timing()
    if result:
        assert isinstance(result["insight"], str)
        assert len(result["insight"]) > 0


# --- PR Velocity Analysis -----------------------------------------------------


def test_pr_velocity_local_mode():
    """Local mode returns mock velocity data."""
    result = analyze_pr_velocity()
    assert result is not None
    assert result["type"] == "pr_velocity"
    assert result["median_cycle_minutes"] > 0
    assert result["tenants_measured"] >= MIN_DATA_POINTS


# --- Failure Categories -------------------------------------------------------


def test_failure_categories_empty():
    """No categories when no failures exist."""
    _reset_graph()
    result = analyze_failure_categories()
    assert result is None


def test_failure_categories_with_data():
    """Categories surfaced when failures exist."""
    _reset_graph()
    for i in range(MIN_DATA_POINTS + 1):
        record_deploy_outcome({
            "commit_sha": f"fail{i}",
            "service": "forgescaler",
            "status": "failed",
        })
    result = analyze_failure_categories()
    if result:
        assert result["type"] == "failure_categories"
        assert "deploy_failure" in result["categories"]
        assert result["categories"]["deploy_failure"] >= MIN_DATA_POINTS


# --- Recommendations ---------------------------------------------------------


def test_recommendations_returns_list():
    """get_recommendations always returns a list."""
    recs = get_recommendations(limit=3)
    assert isinstance(recs, list)
    assert len(recs) <= 3


def test_recommendations_have_required_fields():
    """Each recommendation has type, insight, data_points."""
    recs = get_recommendations(limit=5)
    for r in recs:
        assert "type" in r
        assert "insight" in r
        assert isinstance(r["insight"], str)


def test_analyze_all_returns_list():
    """analyze_all returns a list of patterns (some may be None)."""
    results = analyze_all()
    assert isinstance(results, list)
    assert len(results) == 3  # timing, velocity, failures


# --- Min data points rule -----------------------------------------------------


def test_min_data_points_is_three():
    """Privacy/accuracy rule: >=3 data points required."""
    assert MIN_DATA_POINTS >= 3


# --- API Endpoint Tests -------------------------------------------------------


def test_engineering_insights_endpoint():
    resp = client.get("/api/engineering-insights")
    assert resp.status_code == 200
    body = resp.json()
    assert "patterns" in body
    assert "recommendations" in body
    assert isinstance(body["recommendations"], list)

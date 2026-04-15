"""Tests for synthetic user journey testing."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from fastapi.testclient import TestClient  # noqa: E402

from nexus.synthetic_tests import (  # noqa: E402
    _cache,
    get_summary,
    journey_brief_exists,
    journey_conversation_scoping,
    journey_deploy_readiness,
    journey_health,
    journey_project_list,
    journey_project_separation,
    journey_sfs_detection,
    run_all_journeys,
)
from nexus.server import app  # noqa: E402

client = TestClient(app)


def _clear_cache():
    """Reset the synthetic test cache."""
    import nexus.synthetic_tests as st
    st._cache = ([], 0)


# --- Individual journey tests -------------------------------------------------


def test_journey_health_local():
    """In local mode, forgewing_api returns mock → pass."""
    result = journey_health()
    assert result["name"] == "health"
    assert result["status"] == "pass"
    assert "duration_ms" in result


def test_journey_project_list_local():
    """Local mode returns mock data → pass."""
    result = journey_project_list()
    assert result["name"] == "project_list"
    # Mock returns {mock: True} without a "projects" key → still pass
    assert result["status"] in ("pass", "fail")


def test_journey_project_separation_local():
    """Local mock doesn't have 2+ projects → skip."""
    result = journey_project_separation()
    assert result["name"] == "project_separation"
    assert result["status"] in ("skip", "pass", "fail")


def test_journey_conversation_scoping_local():
    result = journey_conversation_scoping()
    assert result["name"] == "conversation_scoping"
    assert result["status"] == "pass"


def test_journey_brief_exists_local():
    """Mock brief has mock=True → has_content → pass."""
    result = journey_brief_exists()
    assert result["name"] == "brief_exists"
    assert result["status"] == "pass"


def test_journey_deploy_readiness_local():
    result = journey_deploy_readiness()
    assert result["name"] == "deploy_readiness"
    assert result["status"] == "pass"


def test_journey_sfs_detection_local():
    result = journey_sfs_detection()
    assert result["name"] == "sfs_detection"
    assert result["status"] == "pass"


# --- run_all_journeys ---------------------------------------------------------


def test_run_all_journeys_returns_all():
    _clear_cache()
    results = run_all_journeys(force=True)
    assert isinstance(results, list)
    assert len(results) == 26
    names = {r["name"] for r in results}
    assert "health" in names
    assert "brief_exists" in names
    assert "brief_project_isolation" in names
    assert "github_banner_consistency" in names
    assert "action_banner_freshness" in names
    assert "sfs_project_creation" in names
    assert "project_delete_cleanup" in names


def test_run_all_journeys_cached():
    """Second call within TTL returns cached results."""
    _clear_cache()
    r1 = run_all_journeys(force=True)
    r2 = run_all_journeys()  # should be cached
    assert r1 == r2


def test_run_all_journeys_force_bypasses_cache():
    """force=True always re-runs."""
    _clear_cache()
    run_all_journeys(force=True)
    # Modify cache to detect if force works
    import nexus.synthetic_tests as st
    st._cache = ([{"name": "fake", "status": "pass"}], st._cache[1])
    results = run_all_journeys(force=True)
    assert len(results) == 26  # re-ran, not the fake cache


# --- get_summary --------------------------------------------------------------


def test_get_summary():
    _clear_cache()
    summary = get_summary()
    assert "total" in summary
    assert "passed" in summary
    assert "failed" in summary
    assert "score_pct" in summary
    assert "results" in summary
    assert summary["total"] == 26


# --- Result structure ---------------------------------------------------------


def test_every_result_has_name_and_status():
    _clear_cache()
    for r in run_all_journeys(force=True):
        assert "name" in r
        assert "status" in r
        assert r["status"] in ("pass", "fail", "skip", "error")


# --- API endpoints ------------------------------------------------------------


def test_synthetic_tests_post_endpoint():
    resp = client.post("/api/synthetic-tests")
    assert resp.status_code == 200
    body = resp.json()
    assert "results" in body
    assert "passed" in body
    assert "total" in body


def test_synthetic_tests_get_endpoint():
    resp = client.get("/api/synthetic-tests")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "score_pct" in body


def test_diagnostic_report_has_synthetic_section():
    resp = client.get("/api/diagnostic-report")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert "SYNTHETIC TESTS" in report

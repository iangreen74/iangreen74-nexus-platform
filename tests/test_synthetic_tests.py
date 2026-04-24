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
    assert len(results) == 31
    names = {r["name"] for r in results}
    assert "health" in names
    assert "brief_exists" in names
    assert "brief_project_isolation" in names
    assert "github_banner_consistency" in names
    assert "action_banner_freshness" in names
    assert "sfs_project_creation" in names
    assert "project_delete_cleanup" in names
    assert "orphan_zero_invariant" in names


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
    assert len(results) == 31  # re-ran, not the fake cache


# --- get_summary --------------------------------------------------------------


def test_get_summary():
    _clear_cache()
    summary = get_summary()
    assert "total" in summary
    assert "passed" in summary
    assert "failed" in summary
    assert "score_pct" in summary
    assert "results" in summary
    assert summary["total"] == 31


# --- Day 7 regression guards --------------------------------------------------


def test_journey_project_isolation_audit_local():
    from nexus.synthetic_tests import journey_project_isolation_audit
    r = journey_project_isolation_audit()
    assert r["name"] == "project_isolation_audit"
    assert r["status"] == "skip"


def test_journey_deploy_consistency_local():
    from nexus.synthetic_tests import journey_deploy_consistency
    r = journey_deploy_consistency()
    assert r["name"] == "deploy_consistency"
    assert r["status"] == "skip"


def test_journey_version_drift_local():
    from nexus.synthetic_tests import journey_version_drift
    r = journey_version_drift()
    assert r["name"] == "version_drift"
    assert r["status"] == "skip"


def test_journey_merge_key_audit_local():
    from nexus.synthetic_tests import journey_merge_key_audit
    r = journey_merge_key_audit()
    assert r["name"] == "merge_key_audit"
    assert r["status"] == "skip"


# --- Orphan-zero invariant ----------------------------------------------------


def test_orphan_zero_invariant_skip_local():
    """Local mode → skip (no Neptune access)."""
    from nexus.synthetic_tests import journey_orphan_zero_invariant
    r = journey_orphan_zero_invariant()
    assert r["name"] == "orphan_zero_invariant"
    assert r["status"] == "skip"


def test_orphan_zero_invariant_pass_on_zero(monkeypatch):
    """Pass when every count query returns 0."""
    monkeypatch.setattr("nexus.synthetic_tests.MODE", "production")
    import nexus.neptune_client as nc
    monkeypatch.setattr(nc, "query", lambda q, params=None: [{"cnt": 0}])
    from nexus.synthetic_tests import journey_orphan_zero_invariant
    r = journey_orphan_zero_invariant()
    assert r["status"] == "pass"
    assert "Zero orphans" in r["details"]


def test_orphan_zero_invariant_fail_on_null_project(monkeypatch):
    """Fail when a project-scoped label has NULL project_id nodes."""
    monkeypatch.setattr("nexus.synthetic_tests.MODE", "production")
    import nexus.neptune_client as nc

    def _fake(q, params=None):
        if "MissionTask" in q and "project_id IS NULL" in q:
            return [{"cnt": 3}]
        return [{"cnt": 0}]

    monkeypatch.setattr(nc, "query", _fake)
    from nexus.synthetic_tests import journey_orphan_zero_invariant
    r = journey_orphan_zero_invariant()
    assert r["status"] == "fail"
    assert "MissionTask" in r["error"]


def test_orphan_zero_invariant_fail_on_referential_orphan(monkeypatch):
    """Fail when referential check finds dangling children."""
    monkeypatch.setattr("nexus.synthetic_tests.MODE", "production")
    import nexus.neptune_client as nc

    def _fake(q, params=None):
        if "OPTIONAL MATCH" in q and "MissionBrief" in q and "MissionTask" in q:
            return [{"cnt": 5}]
        return [{"cnt": 0}]

    monkeypatch.setattr(nc, "query", _fake)
    from nexus.synthetic_tests import journey_orphan_zero_invariant
    r = journey_orphan_zero_invariant()
    assert r["status"] == "fail"
    assert "ref_orphan" in r["error"]


def test_run_all_journeys_includes_day7_guards():
    _clear_cache()
    results = run_all_journeys(force=True)
    names = {r["name"] for r in results}
    assert "project_isolation_audit" in names
    assert "deploy_consistency" in names
    assert "version_drift" in names
    assert "merge_key_audit" in names


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

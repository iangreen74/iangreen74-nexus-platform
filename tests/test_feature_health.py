"""Tests for feature_health + feature_checks + feature_diagnosis."""
import asyncio
import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import feature_checks as fc  # noqa: E402
from nexus.capabilities import feature_diagnosis as fd  # noqa: E402
from nexus.capabilities import feature_health as fh  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- registry consistency ----------------------------------------------------


def test_six_features_registered():
    assert len(fh.FEATURES) == 6
    assert set(fh.FEATURES.keys()) == {
        "projects", "aria_chat", "code_generation",
        "deployment", "onboarding", "intelligence",
    }


def test_every_feature_has_required_fields():
    for fid, fdef in fh.FEATURES.items():
        assert "name" in fdef
        assert "description" in fdef
        assert "icon" in fdef
        assert "synthetic_tests" in fdef
        assert "health_checks" in fdef


def test_every_referenced_check_exists_in_registry():
    for fid, fdef in fh.FEATURES.items():
        for check in fdef["health_checks"]:
            assert check in fc.HEALTH_CHECKS, f"{fid} references missing check {check}"


def test_health_check_registry_covers_every_function():
    """No orphan check functions hanging around."""
    assert len(fc.HEALTH_CHECKS) == 12


# --- individual check shape --------------------------------------------------


def test_all_checks_return_status_dict():
    for name, fn in fc.HEALTH_CHECKS.items():
        r = fn()
        assert isinstance(r, dict), f"{name} didn't return dict"
        assert "status" in r, f"{name} missing status"
        assert r["status"] in ("ok", "warning", "error"), f"{name} bad status"


def test_check_never_raises_even_on_import_failure():
    """A check function that internally raises should surface as a dict."""
    # check_chat_health calls forgewing_api; in local mode it returns ok.
    # If it raised, this would blow up.
    r = fc.check_chat_health()
    assert r["status"] in ("ok", "warning", "error")


# --- evaluator ---------------------------------------------------------------


def test_evaluate_feature_healthy_when_no_issues():
    fdef = fh.FEATURES["intelligence"]
    r = fh._evaluate_feature("intelligence", fdef, {"brief_exists": "pass", "status_scoped": "pass"})
    assert r["status"] == "healthy"
    assert r["errors"] == 0


def test_evaluate_feature_degraded_on_single_error():
    fdef = fh.FEATURES["aria_chat"]
    # Mark one synthetic as failing
    r = fh._evaluate_feature(
        "aria_chat", fdef,
        {"conversation_scoping": "fail", "conversation_no_bleed": "pass", "conversation_scoped": "pass"},
    )
    assert r["status"] == "degraded"
    assert r["errors"] == 1


def test_evaluate_feature_critical_on_multiple_errors():
    fdef = fh.FEATURES["aria_chat"]
    r = fh._evaluate_feature(
        "aria_chat", fdef,
        {"conversation_scoping": "fail", "conversation_no_bleed": "fail", "conversation_scoped": "pass"},
    )
    assert r["status"] == "critical"
    assert r["errors"] == 2


def test_evaluate_feature_skip_does_not_error():
    """Skipped synthetic journey counts as healthy signal."""
    fdef = fh.FEATURES["projects"]
    r = fh._evaluate_feature(
        "projects", fdef,
        {"project_list": "pass", "project_separation": "skip", "sfs_detection": "pass"},
    )
    assert r["status"] == "healthy"


def test_evaluate_feature_warns_on_check_returning_warning():
    fdef = {"name": "X", "description": "d", "icon": "?",
            "synthetic_tests": [], "health_checks": ["check_project_isolation"]}
    with patch.dict(fc.HEALTH_CHECKS,
                    {"check_project_isolation": lambda: {"status": "warning", "message": "bad"}}):
        r = fh._evaluate_feature("x", fdef, {})
    assert r["status"] == "warning"
    assert r["warnings"] == 1
    assert "bad" in r["warning_details"]


def test_evaluate_feature_handles_check_exception():
    fdef = {"name": "X", "description": "d", "icon": "?",
            "synthetic_tests": [], "health_checks": ["boom"]}
    with patch.dict(fc.HEALTH_CHECKS, {"boom": lambda: (_ for _ in ()).throw(RuntimeError("k"))}):
        r = fh._evaluate_feature("x", fdef, {})
    assert r["warnings"] == 1
    assert "RuntimeError" in r["warning_details"][0]


# --- get_all_feature_health --------------------------------------------------


def test_get_all_feature_health_shape():
    r = _run(fh.get_all_feature_health())
    assert "overall" in r
    assert "features" in r
    assert "timestamp" in r
    assert r["overall"] in ("healthy", "warning", "degraded", "critical")
    assert len(r["features"]) == 6


def test_overall_is_worst_feature_status():
    """If one feature has an error, overall is at least degraded."""
    with patch.dict(fc.HEALTH_CHECKS,
                    {"check_chat_health": lambda: {"status": "error", "message": "down"}}):
        r = _run(fh.get_all_feature_health())
    assert r["overall"] in ("degraded", "critical")


# --- feature_diagnosis -------------------------------------------------------


def _clear_jobs():
    """Clear the module-level job store so tests don't interfere."""
    fd._active_diagnoses.clear()


def _wait_done(job_id: str, timeout: float = 15.0) -> dict:
    """Poll the in-process job store until status != 'running'/'starting'."""
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        rec = _run(fd.get_diagnosis(job_id))
        if rec.get("status") in ("complete", "failed"):
            return rec
        _time.sleep(0.05)
    return rec


def test_start_diagnosis_rejects_invalid_level():
    r = _run(fd.start_diagnosis("projects", level="invalid"))
    assert "error" in r


def test_start_diagnosis_rejects_unknown_feature():
    r = _run(fd.start_diagnosis("nope", level="feature"))
    assert "error" in r


def test_second_start_while_running_queues():
    """Only one diagnosis runs at a time — second call gets its own
    job_id with status='queued' and waits its turn."""
    fd._active_diagnoses.clear()
    fd._diagnosis_queue.clear()
    import time as _time
    fd._active_diagnoses["diag-running"] = {
        "job_id": "diag-running", "target_id": "projects", "level": "feature",
        "status": "running", "phase_label": "Phase 1: Quick check",
        "_start_ts": _time.time(),
    }
    try:
        r = _run(fd.start_diagnosis("aria_chat", level="feature"))
    finally:
        fd._active_diagnoses.pop("diag-running", None)
        fd._diagnosis_queue.clear()
    assert r["status"] == "queued"
    assert r["job_id"] != "diag-running"
    assert r["target_id"] == "aria_chat"
    assert "queued" in r["phase_label"].lower() or "waiting" in r["phase_label"].lower()


def test_start_diagnosis_returns_job_record_immediately():
    _clear_jobs()
    r = _run(fd.start_diagnosis("projects", level="feature"))
    assert "error" not in r
    assert "job_id" in r
    assert r["status"] in ("starting", "running", "complete", "failed")
    assert r["level"] == "feature"


def test_feature_diagnosis_completes_and_produces_report():
    _clear_jobs()
    r = _run(fd.start_diagnosis("projects", level="feature"))
    rec = _wait_done(r["job_id"])
    assert rec["status"] in ("complete", "failed")
    assert rec["report"] is not None
    assert "Projects" in rec["report"] or "projects" in rec["report"]


def test_goal_diagnosis_completes():
    _clear_jobs()
    r = _run(fd.start_diagnosis("platform", level="goal"))
    rec = _wait_done(r["job_id"])
    assert rec["status"] in ("complete", "failed")
    assert rec["report"] is not None
    assert "Goal" in rec["report"] or "goal" in rec["report"]


def test_tenant_diagnosis_completes():
    _clear_jobs()
    r = _run(fd.start_diagnosis("tenant-x", level="tenant"))
    rec = _wait_done(r["job_id"])
    assert rec["status"] in ("complete", "failed")
    assert rec["report"] is not None


def test_get_diagnosis_unknown_job_returns_error():
    r = _run(fd.get_diagnosis("diag-nope"))
    assert "error" in r


def test_report_records_all_phases():
    _clear_jobs()
    r = _run(fd.start_diagnosis("projects", level="feature"))
    rec = _wait_done(r["job_id"])
    # At minimum, phase 1 runs; Phase 2 may also run depending on confidence.
    assert len(rec["phases_completed"]) >= 1
    # Report timeline section lists phases.
    assert "## Timeline" in rec["report"]

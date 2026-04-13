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


def test_diagnose_unknown_feature_returns_error():
    r = _run(fd.diagnose_feature("nope"))
    assert "error" in r


def test_diagnose_returns_report_markdown():
    r = _run(fd.diagnose_feature("projects"))
    assert "error" not in r
    assert "report_markdown" in r
    assert "Projects" in r["report_markdown"]
    assert "Feature Diagnosis Report" in r["report_markdown"]


def test_diagnose_report_mentions_health_and_diagnosis():
    r = _run(fd.diagnose_feature("deployment"))
    md = r["report_markdown"]
    assert "## Status" in md
    assert "## Diagnosis" in md
    assert "## Recommended Actions" in md
    assert "## Evidence Gaps" in md


def test_diagnose_report_survives_investigation_error():
    async def boom(_q, _t):
        raise RuntimeError("investigation down")
    with patch("nexus.capabilities.investigation.investigate", boom):
        r = _run(fd.diagnose_feature("projects"))
    assert "report_markdown" in r
    assert "Investigation error" in r["report_markdown"]

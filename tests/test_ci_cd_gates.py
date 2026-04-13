"""Tests for the CI/CD gate capabilities."""
import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import ci_cd_gates as g  # noqa: E402


# --- check_deploy_drift ------------------------------------------------------


def test_drift_local_mode_returns_mock_aligned():
    r = g.check_deploy_drift()
    assert r["drift"] is False
    assert r["recommendation"] == "ALIGNED"
    assert r.get("mock") is True


# --- evaluate_ci_gate --------------------------------------------------------


def test_gate_engine_hold_maps_to_hold():
    with patch("nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
               return_value={"decision": "HOLD", "blockers": ["incidents"],
                             "warnings": [], "factors": {}, "reason": "r"}):
        r = g.evaluate_ci_gate(commit_sha="abc1234")
    assert r["decision"] == "HOLD"
    assert r["engine_decision"] == "HOLD"
    assert r["commit"] == "abc1234"
    assert "incidents" in r["blockers"]


def test_gate_engine_deploy_maps_to_deploy():
    with patch("nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
               return_value={"decision": "DEPLOY", "blockers": [],
                             "warnings": [], "factors": {}, "reason": "ok"}):
        r = g.evaluate_ci_gate()
    assert r["decision"] == "DEPLOY"
    assert r["engine_decision"] == "DEPLOY"


def test_gate_engine_canary_maps_to_deploy_with_warnings():
    """CANARY isn't a deploy verdict a CI pipeline can act on — we treat
    it as DEPLOY with warnings carried through."""
    with patch("nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
               return_value={"decision": "CANARY", "blockers": [],
                             "warnings": ["a", "b", "c"], "factors": {}, "reason": "r"}):
        r = g.evaluate_ci_gate()
    assert r["decision"] == "DEPLOY"
    assert r["engine_decision"] == "CANARY"
    assert len(r["warnings"]) == 3


def test_gate_fails_open_when_engine_raises():
    """A broken decision engine must not block legitimate deploys."""
    with patch("nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
               side_effect=RuntimeError("nope")):
        r = g.evaluate_ci_gate(commit_sha="x")
    assert r["decision"] == "DEPLOY"
    assert r["fail_open"] is True
    assert any("unavailable" in w for w in r["warnings"])


def test_gate_includes_timestamp_and_factor_count():
    with patch("nexus.capabilities.ci_decision_engine.evaluate_deploy_readiness",
               return_value={"decision": "DEPLOY", "blockers": [], "warnings": [],
                             "factors": {"a": {}, "b": {}, "c": {}}, "reason": ""}):
        r = g.evaluate_ci_gate()
    assert r["checks_run"] == 3
    assert "T" in r["timestamp"]


# --- run_synthetic_suite -----------------------------------------------------


def _fake_results(*statuses):
    return [{"name": f"j{i}", "status": s} for i, s in enumerate(statuses)]


def test_synthetic_all_pass_returns_pass():
    with patch("nexus.synthetic_tests.run_all_journeys",
               return_value=_fake_results("pass", "pass", "pass")):
        r = g.run_synthetic_suite(trigger="t", commit="c")
    assert r["verdict"] == "PASS"
    assert r["passed"] == 3
    assert r["total"] == 3
    assert r["failed_tests"] == []
    assert r["trigger"] == "t"
    assert r["commit"] == "c"


def test_synthetic_one_fail_returns_degraded():
    with patch("nexus.synthetic_tests.run_all_journeys",
               return_value=_fake_results("pass", "fail", "pass")):
        r = g.run_synthetic_suite()
    assert r["verdict"] == "DEGRADED"
    assert r["failed_tests"] == ["j1"]


def test_synthetic_skip_does_not_count_against_pass():
    """Skipped journeys (e.g. project_separation without 2+ projects) should
    not flip the verdict to DEGRADED."""
    with patch("nexus.synthetic_tests.run_all_journeys",
               return_value=_fake_results("pass", "skip", "pass")):
        r = g.run_synthetic_suite()
    assert r["verdict"] == "PASS"
    assert r["skipped"] == 1
    assert r["effective_total"] == 2


def test_synthetic_all_skip_is_empty_not_pass():
    with patch("nexus.synthetic_tests.run_all_journeys",
               return_value=_fake_results("skip", "skip")):
        r = g.run_synthetic_suite()
    assert r["verdict"] == "EMPTY"


def test_synthetic_error_returns_error_verdict():
    with patch("nexus.synthetic_tests.run_all_journeys",
               side_effect=RuntimeError("bedrock down")):
        r = g.run_synthetic_suite()
    assert r["verdict"] == "ERROR"
    assert "bedrock down" in r["error"]


# --- verify_deploy -----------------------------------------------------------


def test_verify_deploy_verified_when_aligned_and_pass():
    with patch.object(g, "check_deploy_drift",
                      return_value={"drift": False, "services": {}, "unique_digests": [],
                                    "recommendation": "ALIGNED"}), \
         patch.object(g, "run_synthetic_suite",
                      return_value={"verdict": "PASS", "passed": 12, "total": 12,
                                    "failed_tests": [], "skipped": 0, "effective_total": 12,
                                    "trigger": "deploy-verify", "commit": "abc",
                                    "results": []}):
        r = g.verify_deploy(expected_sha="abc")
    assert r["verdict"] == "VERIFIED"
    assert r["expected_commit"] == "abc"


def test_verify_deploy_issues_when_drift():
    with patch.object(g, "check_deploy_drift",
                      return_value={"drift": True, "services": {}, "unique_digests": ["a", "b"],
                                    "recommendation": "DRIFT_DETECTED"}), \
         patch.object(g, "run_synthetic_suite",
                      return_value={"verdict": "PASS", "passed": 12, "total": 12,
                                    "failed_tests": [], "skipped": 0, "effective_total": 12,
                                    "trigger": "deploy-verify", "commit": "",
                                    "results": []}):
        r = g.verify_deploy()
    assert r["verdict"] == "ISSUES_DETECTED"


def test_verify_deploy_issues_when_tests_degraded():
    with patch.object(g, "check_deploy_drift",
                      return_value={"drift": False, "services": {}, "unique_digests": [],
                                    "recommendation": "ALIGNED"}), \
         patch.object(g, "run_synthetic_suite",
                      return_value={"verdict": "DEGRADED", "passed": 11, "total": 12,
                                    "failed_tests": ["x"], "skipped": 0, "effective_total": 12,
                                    "trigger": "deploy-verify", "commit": "",
                                    "results": []}):
        r = g.verify_deploy()
    assert r["verdict"] == "ISSUES_DETECTED"

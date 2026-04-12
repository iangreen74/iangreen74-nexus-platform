"""Tests for the CI Decision Engine."""
import os
from unittest.mock import patch

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.capabilities import ci_decision_engine as cde  # noqa: E402


# --- helpers -----------------------------------------------------------------

_PASS = {"status": "pass", "detail": "ok"}
_WARN = {"status": "warn", "detail": "degraded"}
_BLOCK = {"status": "block", "detail": "fatal"}

_FACTOR_FNS = [
    "_check_ci_tests", "_check_code_health", "_check_incidents",
    "_check_tenant_health", "_check_synthetic_tests", "_check_deploy_history",
    "_check_heal_chains", "_check_system_stability",
]


def _run(**overrides):
    """Run evaluate_deploy_readiness with factor overrides."""
    patches = []
    for fn in _FACTOR_FNS:
        result = overrides.get(fn, _PASS)
        patches.append(patch.object(cde, fn, return_value=result))
    for p in patches:
        p.start()
    try:
        return cde.evaluate_deploy_readiness()
    finally:
        for p in patches:
            p.stop()


# --- decision logic ----------------------------------------------------------


def test_all_pass_returns_deploy():
    r = _run()
    assert r["decision"] == "DEPLOY"
    assert r["blockers"] == []
    assert r["warnings"] == []
    assert "All factors green" in r["reason"]


def test_single_block_returns_hold():
    r = _run(_check_ci_tests=_BLOCK)
    assert r["decision"] == "HOLD"
    assert "ci_tests" in r["blockers"]


def test_block_wins_over_warnings():
    r = _run(
        _check_ci_tests=_BLOCK,
        _check_code_health=_WARN,
        _check_incidents=_WARN,
        _check_tenant_health=_WARN,
    )
    assert r["decision"] == "HOLD"
    assert "ci_tests" in r["blockers"]


def test_three_warnings_returns_canary():
    r = _run(
        _check_code_health=_WARN,
        _check_incidents=_WARN,
        _check_tenant_health=_WARN,
    )
    assert r["decision"] == "CANARY"
    assert set(r["warnings"]) == {"code_health", "incidents", "tenant_health"}


def test_two_warnings_returns_deploy_with_note():
    r = _run(_check_code_health=_WARN, _check_incidents=_WARN)
    assert r["decision"] == "DEPLOY"
    assert "Warnings noted" in r["reason"]


def test_decision_has_all_eight_factors():
    r = _run()
    assert len(r["factors"]) == 8
    assert set(r["factors"].keys()) == {
        "ci_tests", "code_health", "incidents", "tenant_health",
        "synthetic_tests", "deploy_history", "heal_chains", "system_stability",
    }


def test_decision_has_timestamp():
    r = _run()
    assert "timestamp" in r
    assert "T" in r["timestamp"]  # ISO format


def test_factor_exception_becomes_warn():
    """A factor function that raises must not abort — downgraded to warn."""
    def bad():
        raise RuntimeError("nope")
    with patch.object(cde, "_check_ci_tests", side_effect=bad):
        # Other factors mocked to pass so we isolate the raise behavior.
        others = [p for p in _FACTOR_FNS if p != "_check_ci_tests"]
        patches = [patch.object(cde, fn, return_value=_PASS) for fn in others]
        for p in patches:
            p.start()
        try:
            r = cde.evaluate_deploy_readiness()
        finally:
            for p in patches:
                p.stop()
    assert r["factors"]["ci_tests"]["status"] == "warn"
    assert "RuntimeError" in r["factors"]["ci_tests"]["detail"]


def test_evaluate_never_raises_in_local_mode():
    """Smoke: real data sources return empty in local mode, engine still
    produces a valid decision."""
    r = cde.evaluate_deploy_readiness()
    assert r["decision"] in ("DEPLOY", "HOLD", "CANARY")
    assert len(r["factors"]) == 8


def test_format_for_report_includes_all_factors():
    r = _run()
    text = cde.format_for_report(r)
    assert "DEPLOY READINESS:" in text
    for name in r["factors"]:
        assert name in text


def test_format_for_report_calls_evaluator_when_no_summary():
    text = cde.format_for_report()
    assert "DEPLOY READINESS:" in text

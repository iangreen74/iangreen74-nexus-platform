"""Tests for CI regression guard (Class 3)."""
import json
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus import overwatch_graph  # noqa: E402
from nexus.capabilities.regression_guard import (  # noqa: E402
    check_regressions,
    format_for_report,
)


def _reset_graph():
    for v in overwatch_graph._local_store.values():
        v.clear()


def _store_audit(report: dict) -> None:
    """Seed a code_audit event into the graph."""
    overwatch_graph.record_event(
        event_type="code_audit",
        service="aria-platform",
        severity="info",
        details={
            "health_score": report.get("health_score", 0),
            "report": json.dumps(report),
        },
    )


def _audit(score: int, critical: int = 0, findings: list | None = None) -> dict:
    return {
        "status": "complete",
        "health_score": score,
        "critical": critical,
        "high": 0,
        "medium": 0,
        "low": 0,
        "findings": findings or [],
    }


def test_insufficient_history():
    """0 or 1 audits → insufficient_history."""
    _reset_graph()
    assert check_regressions()["status"] == "insufficient_history"
    _store_audit(_audit(85))
    assert check_regressions()["status"] == "insufficient_history"


def test_clean_no_regressions():
    """Same score + same findings → clean."""
    _reset_graph()
    _store_audit(_audit(85))
    _store_audit(_audit(85))
    result = check_regressions()
    assert result["status"] == "clean"
    assert result["regressions"] == []


def test_improvement_is_clean():
    """Higher score → clean, no regressions."""
    _reset_graph()
    _store_audit(_audit(70))  # older
    _store_audit(_audit(85))  # newer (events load newest-first)
    result = check_regressions()
    assert result["status"] == "clean"
    assert result["score_delta"] == 15


def test_health_score_drop():
    """Score drop of >5 → regression."""
    _reset_graph()
    _store_audit(_audit(90))  # older
    _store_audit(_audit(75))  # newer
    result = check_regressions()
    assert result["status"] == "regressed"
    assert any(r["kind"] == "health_score_drop" for r in result["regressions"])


def test_small_drop_not_flagged():
    """Score drop of <=5 → no regression."""
    _reset_graph()
    _store_audit(_audit(85))
    _store_audit(_audit(82))
    result = check_regressions()
    assert not any(r["kind"] == "health_score_drop" for r in result["regressions"])


def test_new_critical_findings():
    _reset_graph()
    _store_audit(_audit(80, critical=2))  # older
    _store_audit(_audit(75, critical=5))  # newer
    result = check_regressions()
    assert any(r["kind"] == "new_critical_findings" for r in result["regressions"])


def test_file_limit_breach():
    _reset_graph()
    prev = _audit(85, findings=[
        {"rule": "file_limits", "file": "old.py", "line": 210},
    ])
    curr = _audit(85, findings=[
        {"rule": "file_limits", "file": "old.py", "line": 210},
        {"rule": "file_limits", "file": "new_big.py", "line": 250},
    ])
    _store_audit(prev)
    _store_audit(curr)
    result = check_regressions()
    assert any(r["kind"] == "file_limit_breach" for r in result["regressions"])


def test_isolation_regression():
    _reset_graph()
    prev = _audit(85, findings=[])
    curr = _audit(70, critical=1, findings=[
        {"rule": "unscoped_queries", "file": "new.py", "line": 42},
    ])
    _store_audit(prev)
    _store_audit(curr)
    result = check_regressions()
    assert any(r["kind"] == "isolation_regression" for r in result["regressions"])


def test_removed_finding_not_regression():
    """Finding removed in current → no regression (improvement)."""
    _reset_graph()
    prev = _audit(70, findings=[
        {"rule": "unscoped_queries", "file": "old.py", "line": 10},
    ])
    curr = _audit(85, findings=[])
    _store_audit(prev)
    _store_audit(curr)
    result = check_regressions()
    assert not any(r["kind"] == "isolation_regression" for r in result["regressions"])


def test_regression_stored_in_graph():
    _reset_graph()
    _store_audit(_audit(85))
    _store_audit(_audit(70))
    check_regressions()
    regression_events = [
        e for e in overwatch_graph._local_store.get("OverwatchPlatformEvent", [])
        if e.get("event_type") == "regression_report"
    ]
    assert len(regression_events) >= 1


def test_format_insufficient():
    text = format_for_report({"status": "insufficient_history",
                              "message": "only 1 audit"})
    assert "CODE REGRESSION" in text
    assert "only 1 audit" in text


def test_format_clean():
    text = format_for_report({
        "status": "clean", "current_score": 85, "previous_score": 85,
        "regressions": [],
    })
    assert "clean" in text


def test_format_regressed():
    text = format_for_report({
        "status": "regressed", "regression_count": 1, "score_delta": -10,
        "regressions": [{"kind": "health_score_drop", "severity": "high",
                         "message": "dropped"}],
    })
    assert "regression" in text.lower()
    assert "health_score_drop" in text

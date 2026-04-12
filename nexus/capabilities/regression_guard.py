"""
CI Regression Guard — compare the current code audit to the previous one.

Triggered when a new CI pass is detected. Loads the current and previous
code_audit events from the Overwatch graph and computes deltas:

1. health_score_drop: score dropped by >5 points
2. new_critical_findings: critical count increased
3. file_limit_breach: new file over 200 lines
4. isolation_regression: new unscoped_queries or untagged_writes

Reports are stored as regression_report events for historical trends.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from nexus import overwatch_graph

logger = logging.getLogger(__name__)

HEALTH_DROP_THRESHOLD = 5
ISOLATION_RULES = {"unscoped_queries", "untagged_writes", "isolation_escapes"}


def check_regressions(commit_sha: str | None = None) -> dict[str, Any]:
    """Compare latest two code_audit reports and return findings."""
    reports = _load_recent_audits(limit=2)
    if len(reports) < 2:
        return {
            "status": "insufficient_history",
            "message": f"Need 2 audits, have {len(reports)}",
            "regressions": [],
        }

    current, previous = reports[0], reports[1]
    regressions: list[dict[str, Any]] = []

    regressions.extend(_check_score_drop(current, previous))
    regressions.extend(_check_new_critical(current, previous))
    regressions.extend(_check_file_breaches(current, previous))
    regressions.extend(_check_isolation_regression(current, previous))

    summary = {
        "status": "clean" if not regressions else "regressed",
        "commit_sha": commit_sha or "",
        "current_score": current.get("health_score", 0),
        "previous_score": previous.get("health_score", 0),
        "score_delta": current.get("health_score", 0) - previous.get("health_score", 0),
        "regression_count": len(regressions),
        "regressions": regressions,
    }
    _store_regression_report(summary)
    return summary


def _load_recent_audits(limit: int = 2) -> list[dict[str, Any]]:
    """Load the N most recent code_audit reports, newest first."""
    reports: list[dict[str, Any]] = []
    events = overwatch_graph.get_recent_events(limit=200)
    for e in events:
        if e.get("event_type") != "code_audit":
            continue
        details = e.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        raw = details.get("report")
        if not raw:
            continue
        try:
            reports.append(json.loads(raw))
        except (ValueError, TypeError):
            continue
        if len(reports) >= limit:
            break
    return reports


def _check_score_drop(current: dict, previous: dict) -> list[dict[str, Any]]:
    cur = current.get("health_score", 0)
    prev = previous.get("health_score", 0)
    if prev - cur > HEALTH_DROP_THRESHOLD:
        return [{
            "kind": "health_score_drop",
            "severity": "high",
            "message": f"Health dropped {prev} → {cur} (Δ{cur - prev})",
            "detail": f"Threshold is {HEALTH_DROP_THRESHOLD}-point drop",
        }]
    return []


def _check_new_critical(current: dict, previous: dict) -> list[dict[str, Any]]:
    cur_c = current.get("critical", 0)
    prev_c = previous.get("critical", 0)
    if cur_c > prev_c:
        return [{
            "kind": "new_critical_findings",
            "severity": "critical",
            "message": f"Critical findings rose {prev_c} → {cur_c} (+{cur_c - prev_c})",
            "detail": "Review new critical findings in latest code_audit",
        }]
    return []


def _findings_for_rule(report: dict, rule: str) -> set[tuple[str, int]]:
    """Extract (file, line) tuples for all findings matching a rule."""
    return {
        (f.get("file", "?"), f.get("line", 0))
        for f in report.get("findings", [])
        if f.get("rule") == rule
    }


def _check_file_breaches(current: dict, previous: dict) -> list[dict[str, Any]]:
    """New files exceeding the 200-line limit since last audit."""
    cur = _findings_for_rule(current, "file_limits")
    prev = _findings_for_rule(previous, "file_limits")
    new = cur - prev
    if not new:
        return []
    files = sorted({f for f, _ in new})[:5]
    return [{
        "kind": "file_limit_breach",
        "severity": "medium",
        "message": f"{len(new)} new file-limit breach(es)",
        "detail": f"Files: {', '.join(files)}",
    }]


def _check_isolation_regression(current: dict, previous: dict) -> list[dict[str, Any]]:
    """Any new unscoped_queries, untagged_writes, or isolation_escapes."""
    findings: list[dict[str, Any]] = []
    for rule in ISOLATION_RULES:
        cur = _findings_for_rule(current, rule)
        prev = _findings_for_rule(previous, rule)
        new = cur - prev
        if new:
            files = sorted({f for f, _ in new})[:3]
            findings.append({
                "kind": "isolation_regression",
                "severity": "critical",
                "message": f"{len(new)} new {rule} finding(s)",
                "detail": f"Files: {', '.join(files)}",
            })
    return findings


def _store_regression_report(summary: dict[str, Any]) -> None:
    """Record the regression check as an event for history."""
    try:
        overwatch_graph.record_event(
            event_type="regression_report",
            service="aria-platform",
            severity="warning" if summary.get("regressions") else "info",
            details={
                "commit_sha": summary.get("commit_sha", ""),
                "score_delta": summary.get("score_delta", 0),
                "regression_count": summary.get("regression_count", 0),
                "summary": json.dumps(summary),
            },
        )
    except Exception:
        logger.debug("Failed to store regression report", exc_info=True)


def format_for_report(summary: dict[str, Any] | None) -> str:
    """Format the regression summary for the diagnostic report."""
    if not summary or summary.get("status") == "insufficient_history":
        msg = (summary or {}).get("message", "no history")
        return f"CODE REGRESSION: {msg}"
    if summary["status"] == "clean":
        return (
            f"CODE REGRESSION: clean (score {summary.get('previous_score', 0)} "
            f"→ {summary.get('current_score', 0)})"
        )
    lines = [
        f"CODE REGRESSION: {summary['regression_count']} regression(s) "
        f"(score Δ{summary.get('score_delta', 0)})"
    ]
    for r in summary.get("regressions", []):
        lines.append(f"  [{r['severity'].upper()}] {r['kind']}: {r['message']}")
    return "\n".join(lines)

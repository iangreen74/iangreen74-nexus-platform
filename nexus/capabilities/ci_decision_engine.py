"""
CI Decision Engine — Overwatch evaluates deploy readiness.

Aggregates 8 platform-health factors into a single DEPLOY / HOLD / CANARY
decision with per-factor reasoning. Every factor is read-only: this
engine never triggers actions, only surfaces a recommendation.

Factor statuses:
  - "pass"  — this factor is healthy
  - "warn"  — degraded but not a hard block
  - "block" — deploy should not proceed until resolved

Decision rules:
  - any block     → HOLD
  - ≥ 3 warnings  → CANARY
  - otherwise     → DEPLOY (note any warnings in the reason)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nexus.ci_decision")

# Thresholds — tuned conservative; raise to tighten the gate.
CRITICAL_FINDINGS_WARN = 200
OPEN_INCIDENTS_BLOCK = 3
OPEN_INCIDENTS_WARN = 1
SYNTHETIC_PASS_WARN_PCT = 80
RECENT_DEPLOY_FAIL_WARN = 2
HEAL_CHAINS_WARN = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _factor(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def evaluate_deploy_readiness() -> dict[str, Any]:
    """Run all 8 checks and synthesize a decision. Never raises."""
    factors: dict[str, dict[str, Any]] = {}
    for name, fn in (
        ("ci_tests", _check_ci_tests),
        ("code_health", _check_code_health),
        ("incidents", _check_incidents),
        ("tenant_health", _check_tenant_health),
        ("synthetic_tests", _check_synthetic_tests),
        ("deploy_history", _check_deploy_history),
        ("heal_chains", _check_heal_chains),
        ("system_stability", _check_system_stability),
    ):
        try:
            factors[name] = fn()
        except Exception as exc:
            logger.debug("factor %s crashed", name, exc_info=True)
            factors[name] = _factor("warn", f"check raised: {type(exc).__name__}: {str(exc)[:100]}")

    blockers = [k for k, v in factors.items() if v.get("status") == "block"]
    warnings = [k for k, v in factors.items() if v.get("status") == "warn"]

    if blockers:
        decision = "HOLD"
        reason = f"Blocked by: {', '.join(blockers)}"
    elif len(warnings) >= 3:
        decision = "CANARY"
        reason = f"{len(warnings)} warnings ({', '.join(warnings)}) — deploy with caution"
    elif warnings:
        decision = "DEPLOY"
        reason = f"Warnings noted ({', '.join(warnings)}) but not blocking"
    else:
        decision = "DEPLOY"
        reason = "All factors green"

    return {
        "decision": decision,
        "reason": reason,
        "factors": factors,
        "blockers": blockers,
        "warnings": warnings,
        "timestamp": _now_iso(),
    }


# --- Individual factor checks -------------------------------------------------


def _check_ci_tests() -> dict[str, Any]:
    """Latest CI summary from the S3 results feed."""
    from nexus.ci_reader import get_ci_health_summary

    s = get_ci_health_summary() or {}
    status = s.get("status", "unavailable")
    if status == "unavailable":
        return _factor("warn", "No CI results in S3")
    failed = s.get("failed_count", 0)
    total = s.get("total_tests", 0)
    if status == "failed" or failed > 0:
        return _factor("block", f"CI failed: {failed}/{total} tests failing")
    return _factor("pass", f"{total} tests passing (commit {str(s.get('commit_sha', ''))[:8]})")


def _check_code_health() -> dict[str, Any]:
    """Latest code audit score + critical count."""
    from nexus.nexus_code_auditor import get_latest_report

    r = get_latest_report()
    if not r:
        return _factor("warn", "No audit report yet — run POST /api/code-audit")
    score = r.get("health_score", 0)
    critical = r.get("critical", 0)
    if critical > CRITICAL_FINDINGS_WARN:
        return _factor("warn", f"Score {score}/100, {critical} critical findings")
    return _factor("pass", f"Score {score}/100, {critical} critical findings")


def _check_incidents() -> dict[str, Any]:
    """Open Overwatch incidents."""
    from nexus import overwatch_graph

    rows = overwatch_graph.get_open_incidents() or []
    cnt = len(rows)
    if cnt >= OPEN_INCIDENTS_BLOCK:
        return _factor("block", f"{cnt} open incidents")
    if cnt >= OPEN_INCIDENTS_WARN:
        return _factor("warn", f"{cnt} open incident(s)")
    return _factor("pass", "No open incidents")


def _check_tenant_health() -> dict[str, Any]:
    """Critical tenants via live tenant-health sensor."""
    from nexus.sensors import tenant_health

    reports = tenant_health.check_all_tenants() or []
    if not reports:
        return _factor("warn", "No tenants checked")
    critical = [r for r in reports if r.get("overall_status") == "critical"]
    if len(critical) >= 2:
        return _factor("warn", f"{len(critical)}/{len(reports)} tenants critical")
    return _factor(
        "pass",
        f"{len(reports)} tenants: {len(critical)} critical",
    )


def _check_synthetic_tests() -> dict[str, Any]:
    """Synthetic-journey pass rate."""
    from nexus.synthetic_tests import get_summary

    s = get_summary() or {}
    passed = s.get("passed", 0)
    total = s.get("total", 0)
    if total == 0:
        return _factor("warn", "No synthetic tests run")
    pct = round(passed / total * 100)
    if pct < SYNTHETIC_PASS_WARN_PCT:
        return _factor("warn", f"{passed}/{total} passing ({pct}%) — threshold {SYNTHETIC_PASS_WARN_PCT}%")
    return _factor("pass", f"{passed}/{total} passing ({pct}%)")


def _check_deploy_history() -> dict[str, Any]:
    """Recent deploy outcome from the S3 feed."""
    from nexus.ci_reader import get_deploy_outcome_summary

    d = get_deploy_outcome_summary() or {}
    if d.get("status") == "unavailable":
        return _factor("pass", "No deploy outcome feed yet")
    status = d.get("status") or "?"
    if status in ("failed", "error"):
        return _factor("warn", f"Last deploy: {status} ({d.get('service', '?')})")
    return _factor("pass", f"Last deploy: {status}")


def _check_heal_chains() -> dict[str, Any]:
    """Active heal chains indicate Overwatch is already busy patching."""
    from nexus.reasoning.executor import get_all_active_chains

    chains = get_all_active_chains() or {}
    cnt = len(chains)
    if cnt >= HEAL_CHAINS_WARN:
        return _factor("warn", f"{cnt} active heal chain(s) in flight")
    return _factor("pass", f"{cnt} active heal chain(s)")


def _check_system_stability() -> dict[str, Any]:
    """Daemon cycle health."""
    from nexus.sensors import daemon_monitor

    d = daemon_monitor.check_daemon() or {}
    if d.get("stale"):
        return _factor("block", f"Daemon stale — last cycle {d.get('cycle_age_minutes', '?')}m ago")
    if not d.get("running"):
        return _factor("block", "Daemon not running")
    errors = d.get("error_count_30m", 0)
    if errors > 5:
        return _factor("warn", f"{errors} daemon errors in last 30m")
    return _factor("pass", f"Daemon healthy ({errors} errors/30m)")


def format_for_report(summary: dict[str, Any] | None = None) -> str:
    """Format the decision for the diagnostic report."""
    if summary is None:
        summary = evaluate_deploy_readiness()
    decision = summary.get("decision", "?")
    reason = summary.get("reason", "")
    lines = [f"DEPLOY READINESS: {decision} — {reason}"]
    for name, f in summary.get("factors", {}).items():
        status = f.get("status", "?")
        marker = {"pass": "[ok]", "warn": "[warn]", "block": "[BLOCK]"}.get(status, "[?]")
        lines.append(f"  {marker} {name}: {f.get('detail', '')}")
    return "\n".join(lines)

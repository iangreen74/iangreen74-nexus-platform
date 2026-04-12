"""
Deploy Decision Engine — Overwatch advises CI on whether to deploy.

CI calls POST /api/deploy-decision before every production deploy.
Overwatch checks platform health, active heal chains, recent failure
rate, risk score, and time of day, then returns DEPLOY / HOLD / CANARY.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus.config import MODE

logger = logging.getLogger(__name__)


def evaluate_deploy_request(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate whether a deploy should proceed.

    Args:
        request: {commit_sha, service, environment, changed_files,
                  risk_score, commit_message}

    Returns:
        {decision: "DEPLOY"|"HOLD"|"CANARY", reason: str, factors: [...]}
    """
    factors: list[dict[str, Any]] = []
    hold_reasons: list[str] = []

    # Factor 1: Platform health
    health = _check_platform_health()
    factors.append({"name": "platform_health", "value": health["status"]})
    if health["status"] == "degraded":
        factors.append({"name": "note", "value": health.get("reason", "")})
    elif health["status"] == "critical":
        hold_reasons.append(f"Platform is CRITICAL: {health.get('reason', '')}")

    # Factor 2: Active heal chains
    active_chains = _count_active_heal_chains()
    factors.append({"name": "active_heal_chains", "value": active_chains})
    if active_chains >= 3:
        hold_reasons.append(
            f"{active_chains} heal chains active — system is mid-recovery"
        )

    # Factor 3: Recent deploy failure rate
    recent_failures = _recent_deploy_failures(hours=6)
    factors.append({"name": "recent_deploy_failures_6h", "value": recent_failures})
    if recent_failures >= 3:
        hold_reasons.append(f"{recent_failures} deploy failures in last 6h")

    # Factor 4: Risk score (from CI risk assessment)
    risk = float(request.get("risk_score", 0))
    factors.append({"name": "risk_score", "value": risk})
    if risk > 0.7:
        hold_reasons.append(f"High risk score: {risk:.0%}")

    # Factor 5: Time of day (off-hours awareness)
    hour_utc = datetime.now(timezone.utc).hour
    is_off_hours = 5 <= hour_utc <= 9  # 9PM-1AM Pacific
    factors.append({"name": "off_hours", "value": is_off_hours})
    if is_off_hours:
        factors.append({"name": "note", "value": "Off-hours deploy"})

    # Factor 6: Tenant deploys in progress
    tenant_deploys = _tenant_deploys_in_progress()
    factors.append({"name": "tenant_deploys_active", "value": tenant_deploys})
    if tenant_deploys > 0:
        factors.append(
            {"name": "note", "value": f"{tenant_deploys} tenant deploy(s) active"}
        )

    # Factor 7: Open incidents
    open_incidents = _count_open_incidents()
    factors.append({"name": "open_incidents", "value": open_incidents})
    if open_incidents >= 2:
        hold_reasons.append(f"{open_incidents} open incidents")

    # Decision
    if hold_reasons:
        decision = "HOLD"
        reason = "; ".join(hold_reasons)
    elif risk > 0.4 or active_chains > 0 or is_off_hours:
        decision = "CANARY"
        reason = "Moderate risk or active recovery — canary recommended"
    else:
        decision = "DEPLOY"
        reason = "All clear — proceed with deployment"

    sha = request.get("commit_sha", "?")[:8]
    svc = request.get("service", "?")
    logger.info("Deploy decision %s/%s: %s — %s", svc, sha, decision, reason)
    return {
        "decision": decision,
        "reason": reason,
        "factors": factors,
        "commit_sha": request.get("commit_sha", ""),
        "service": request.get("service", ""),
    }


# --- Helpers — read from existing Overwatch state ----------------------------


def _check_platform_health() -> dict[str, Any]:
    if MODE != "production":
        return {"status": "healthy", "reason": ""}
    try:
        from nexus.sensors import daemon_monitor, ci_monitor

        daemon = daemon_monitor.check_daemon()
        ci = ci_monitor.check_ci()
        if not daemon.get("running"):
            return {"status": "critical", "reason": "Daemon is DOWN"}
        if daemon.get("stale"):
            return {"status": "degraded", "reason": "Daemon is stale"}
        if ci.get("green_rate_24h") is not None and ci["green_rate_24h"] < 0.5:
            return {"status": "degraded", "reason": "CI green rate below 50%"}
        return {"status": "healthy", "reason": ""}
    except Exception:
        return {"status": "unknown", "reason": "Could not check"}


def _count_active_heal_chains() -> int:
    try:
        from nexus.reasoning.executor import get_all_active_chains

        return len(get_all_active_chains())
    except Exception:
        return 0


def _recent_deploy_failures(hours: int = 6) -> int:
    try:
        from nexus.deploy_patterns import get_deploy_failure_count

        return get_deploy_failure_count(hours=hours)
    except Exception:
        return 0


def _tenant_deploys_in_progress() -> int:
    if MODE != "production":
        return 0
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (d:DeploymentProgress) WHERE d.stage <> 'complete' "
            "AND d.stage <> 'failed' RETURN count(d) AS c"
        )
        return int(rows[0].get("c", 0)) if rows else 0
    except Exception:
        return 0


def _count_open_incidents() -> int:
    try:
        from nexus import overwatch_graph

        return len(overwatch_graph.get_open_incidents())
    except Exception:
        return 0

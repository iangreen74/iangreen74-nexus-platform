"""
Dashboard API Routes.

These endpoints power the operator console. Everything is read-mostly;
the approve endpoint is a placeholder until human-in-the-loop approval
is wired to a real queue.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from nexus.capabilities import alert, ecs_ops  # noqa: F401 — register capabilities
from nexus.capabilities.registry import registry
from nexus.reasoning import triage
from nexus.reasoning.alert_dispatcher import maybe_alert
from nexus.sensors import ci_monitor, daemon_monitor, tenant_health

logger = logging.getLogger("nexus.dashboard")

router = APIRouter()


def _tenant_summary(reports: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"healthy": 0, "degraded": 0, "critical": 0, "pending": 0, "unknown": 0}
    for r in reports:
        status = r.get("overall_status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


@router.get("/status")
async def platform_status() -> dict[str, Any]:
    """Overall control-plane status — daemon + CI + tenant rollup."""
    daemon = daemon_monitor.check_daemon()
    ci = ci_monitor.check_ci()
    tenants = tenant_health.check_all_tenants()
    summary = _tenant_summary(tenants)
    overall = (
        "healthy"
        if daemon.get("healthy") and ci.get("healthy") and summary.get("critical", 0) == 0
        else "degraded"
    )

    # Fire Telegram alerts for any escalation-worthy decisions. Dedup
    # lives inside maybe_alert so the 30s dashboard polling doesn't
    # turn into a notification flood.
    daemon_decision = triage.triage_daemon_health(daemon)
    maybe_alert(
        "daemon", daemon_decision,
        dedup_key="daemon:" + daemon_decision.action,
        context={"running": daemon.get("running"), "stale": daemon.get("stale"),
                 "cycle_age_minutes": daemon.get("cycle_age_minutes")},
    )
    ci_decision = triage.triage_ci_health(ci)
    maybe_alert(
        "ci", ci_decision,
        dedup_key="ci:" + ci_decision.action,
        context={"green_rate_24h": ci.get("green_rate_24h"),
                 "failing_workflows": ",".join(ci.get("failing_workflows", []) or [])},
    )
    for t in tenants:
        decision = triage.triage_tenant_health(t)
        tid = t.get("tenant_id", "unknown")
        maybe_alert(
            f"tenant:{tid}", decision,
            dedup_key=f"tenant:{tid}:{decision.action}",
            context={"overall_status": t.get("overall_status")},
        )

    return {
        "overall": overall,
        "daemon": daemon,
        "ci": ci,
        "tenants": summary,
        "tenant_count": len(tenants),
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    reports = tenant_health.check_all_tenants()
    return {"count": len(reports), "tenants": reports}


@router.get("/tenants/{tenant_id}")
async def tenant_detail(tenant_id: str) -> dict[str, Any]:
    report = tenant_health.check_tenant(tenant_id)
    decision = triage.triage_tenant_health(report)
    return {"report": report, "decision": decision.to_dict()}


@router.get("/daemon")
async def daemon() -> dict[str, Any]:
    report = daemon_monitor.check_daemon()
    decision = triage.triage_daemon_health(report)
    return {"report": report, "decision": decision.to_dict()}


@router.get("/ci")
async def ci() -> dict[str, Any]:
    report = ci_monitor.check_ci()
    decision = triage.triage_ci_health(report)
    return {"report": report, "decision": decision.to_dict()}


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    caps = registry.list_all()
    return {
        "count": len(caps),
        "capabilities": [
            {
                "name": c.name,
                "blast_radius": c.blast_radius,
                "description": c.description,
                "requires_approval": c.requires_approval,
            }
            for c in caps
        ],
    }


@router.get("/actions")
async def actions(limit: int = 50) -> dict[str, Any]:
    recent = registry.recent_actions(limit=limit)
    return {"count": len(recent), "actions": recent}


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str) -> dict[str, Any]:
    """
    Placeholder for human-in-the-loop approval.
    Returns 202 to signal the approval was recorded but not yet acted on.
    """
    logger.info("Approval recorded for %s (placeholder)", action_id)
    return {
        "action_id": action_id,
        "approved": True,
        "note": "Approval queue not yet wired — recorded for later execution.",
    }


@router.get("/triage/event")
async def triage_event(text: str) -> dict[str, Any]:
    """Triage a free-text event string. Useful for operator spot-checks."""
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    decision = triage.triage_event(text)
    return decision.to_dict()

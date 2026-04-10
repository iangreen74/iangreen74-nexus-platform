"""
Dashboard API Routes.

These endpoints power the operator console. Everything is read-mostly;
the approve endpoint is a placeholder until human-in-the-loop approval
is wired to a real queue.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from nexus import overwatch_graph
from nexus.capabilities import alert, ecs_ops  # noqa: F401 — register capabilities
from nexus.capabilities.registry import registry
from nexus.config import AWS_REGION, MODE, OPS_CHAT_MAX_TOKENS, OPS_CHAT_MODEL_ID
from nexus.forge import aria_repo, deploy_manager, fix_generator
from nexus.reasoning import triage
from nexus.reasoning.alert_dispatcher import maybe_alert
from nexus.sensors import (
    ci_monitor,
    daemon_monitor,
    infrastructure_lock,
    preemptive,
    tenant_health,
)

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

    locks = infrastructure_lock.check_locks()
    preemptive_alerts = preemptive.run_preemptive_checks()
    if not locks.get("all_locked"):
        # Lock violations are always critical and override the rollup.
        overall = "degraded"

    return {
        "overall": overall,
        "daemon": daemon,
        "ci": ci,
        "tenants": summary,
        "tenant_count": len(tenants),
        "infrastructure": {
            "all_locked": locks.get("all_locked"),
            "violation_count": locks.get("violation_count", 0),
            "violations": locks.get("violations", []),
        },
        "preemptive": {
            "alert_count": len(preemptive_alerts),
            "alerts": preemptive_alerts,
        },
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


# --- Overwatch graph -----------------------------------------------------
@router.get("/patterns")
async def failure_patterns(min_confidence: float = 0.0) -> dict[str, Any]:
    """Learned FailurePattern nodes from Overwatch's graph."""
    patterns = overwatch_graph.get_failure_patterns(min_confidence=min_confidence)
    return {"count": len(patterns), "patterns": patterns}


@router.get("/investigations")
async def investigations(limit: int = 50) -> dict[str, Any]:
    """Recent DiagnosticInvestigation rows."""
    rows = overwatch_graph.query(
        "MATCH (i:OverwatchInvestigation) RETURN i.id AS id, "
        "i.trigger_event AS trigger_event, i.conclusion AS conclusion, "
        "i.confidence AS confidence, i.outcome AS outcome, "
        "i.created_at AS created_at ORDER BY i.created_at DESC LIMIT 50"
    )
    return {"count": len(rows), "investigations": rows[:limit]}


@router.get("/graph/stats")
async def graph_stats() -> dict[str, Any]:
    """Per-label node counts in the Overwatch graph."""
    stats = overwatch_graph.graph_stats()
    return {"stats": stats, "total": sum(stats.values())}


# --- Forge Engine --------------------------------------------------------
@router.get("/forge/prs")
async def forge_prs() -> dict[str, Any]:
    """PRs Overwatch has opened on aria-platform (filtered by overwatch-fix label)."""
    prs = aria_repo.list_overwatch_prs()
    return {"count": len(prs), "prs": prs}


@router.get("/forge/templates")
async def forge_templates() -> dict[str, Any]:
    """Catalog of fix templates Overwatch can generate."""
    templates = fix_generator.list_known_fix_templates()
    return {"count": len(templates), "templates": templates}


@router.post("/forge/deploy/{service}")
async def forge_deploy(service: str, approve: bool = False) -> dict[str, Any]:
    """
    Trigger a deploy of an aria-platform service.

    Moderate blast radius — requires `?approve=true` so it isn't fired
    by accident from the dashboard. Dangerous services should add their
    own gating in a future revision.
    """
    if not approve:
        raise HTTPException(
            status_code=403,
            detail="approval required: re-call with ?approve=true",
        )
    return deploy_manager.deploy_service(service)


@router.get("/forge/deploy/{service}")
async def forge_deploy_status(service: str) -> dict[str, Any]:
    return deploy_manager.get_deploy_status(service)


# --- Diagnostic Report -----------------------------------------------------
def _format_report(
    status: dict[str, Any],
    tenants: dict[str, Any],
    actions: list[dict[str, Any]],
    patterns: list[dict[str, Any]],
) -> str:
    """Build the human-readable text diagnostic for clipboard paste."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [f"OVERWATCH DIAGNOSTIC — {now}"]
    lines.append(f"Platform: {status.get('overall', 'unknown').upper()}")
    lines.append("")

    daemon = status.get("daemon", {}) or {}
    age = daemon.get("cycle_age_minutes")
    age_str = f"{age:.0f}m" if isinstance(age, (int, float)) else "—"
    lines.append(
        f"Daemon: {'RUNNING' if daemon.get('running') else 'DOWN'} "
        f"(stale={daemon.get('stale')}, last cycle {age_str} ago, "
        f"errors/30m={daemon.get('error_count_30m', 0)})"
    )
    if daemon.get("last_cycle_duration_seconds"):
        lines.append(
            f"  Last cycle duration: {daemon['last_cycle_duration_seconds']}s, "
            f"prs_checked={daemon.get('last_cycle_prs_checked')}, "
            f"tasks_dispatched={daemon.get('last_cycle_tasks_dispatched')}"
        )

    ci = status.get("ci", {}) or {}
    failing = ci.get("failing_workflows") or []
    lines.append(
        f"CI: {(ci.get('green_rate_24h', 0) or 0) * 100:.0f}% green over "
        f"{ci.get('run_count', 0)} runs (failing: {', '.join(failing) or 'none'})"
    )

    ts = status.get("tenants", {}) or {}
    lines.append(
        f"Tenants: {status.get('tenant_count', 0)} total — "
        f"{ts.get('healthy', 0)} healthy, {ts.get('degraded', 0)} degraded, "
        f"{ts.get('critical', 0)} critical, {ts.get('pending', 0)} pending"
    )
    lines.append("")

    for t in tenants.get("tenants", []):
        tid = t.get("tenant_id", "unknown")
        lines.append(f"TENANT {tid}: {(t.get('overall_status') or 'unknown').upper()}")
        deployment = t.get("deployment", {}) or {}
        if deployment.get("provisioned") is False:
            lines.append("  Deployment: NO_STACK (no ForgeScaler-* CF stack matched)")
        else:
            stack = deployment.get("stack") or {}
            lines.append(f"  Deployment: stack={stack.get('stack_name', '—')} status={stack.get('status', '—')}")
        pipeline = t.get("pipeline", {}) or {}
        lines.append(
            f"  Pipeline: stuck_tasks={pipeline.get('stuck_task_count', 0)}, "
            f"in_progress={pipeline.get('tasks_in_progress', 0)}, "
            f"last_pr={pipeline.get('last_pr_at', '—')}"
        )
        conv = t.get("conversation", {}) or {}
        lines.append(
            f"  Conversation: messages={conv.get('message_count', 0)}, "
            f"last_message={conv.get('last_message_at', '—')}, "
            f"inactive={conv.get('inactive')}"
        )
        lines.append("")

    if patterns:
        lines.append("LEARNED FAILURE PATTERNS:")
        for p in patterns[:10]:
            lines.append(
                f"  - {p.get('name')}: {p.get('occurrence_count', 0)}x, "
                f"confidence={(p.get('confidence', 0) or 0) * 100:.0f}%, "
                f"blast={p.get('blast_radius', '—')}"
            )
        lines.append("")

    if actions:
        lines.append("RECENT ACTIONS:")
        for a in actions[:10]:
            outcome = "OK" if a.get("ok") else (a.get("error") or "FAILED")
            lines.append(f"  - {a.get('started_at', '')}  {a.get('name', '')}  →  {outcome}")
        lines.append("")

    lines.append("---")
    lines.append("Paste this into Claude with: 'Diagnose what's wrong and suggest concrete fixes.'")
    return "\n".join(lines)


@router.get("/diagnostic-report")
async def diagnostic_report() -> dict[str, Any]:
    """Generate a structured text diagnostic for pasting into Claude."""
    status = await platform_status()
    tenants = await list_tenants()
    actions_resp = await actions(limit=10)
    patterns_resp = await failure_patterns(min_confidence=0.0)
    text = _format_report(
        status=status,
        tenants=tenants,
        actions=actions_resp.get("actions", []),
        patterns=patterns_resp.get("patterns", []),
    )
    return {"report": text, "generated_at": datetime.now(timezone.utc).isoformat()}


# --- Ops Chat (Bedrock) ----------------------------------------------------
def _build_ops_system_prompt(status: dict[str, Any], tenants: dict[str, Any]) -> str:
    """Compose the system prompt with live platform context."""
    return (
        "You are Ops, the Overwatch platform engineering assistant. You help "
        "Ian diagnose and fix issues with the Forgewing platform.\n\n"
        "Current platform state:\n"
        f"{json.dumps(status, indent=2, default=str)}\n\n"
        "Current tenant health:\n"
        f"{json.dumps(tenants, indent=2, default=str)}\n\n"
        "You have deep knowledge of:\n"
        "- ECS services: forgescaler, forgescaler-staging, aria-daemon, aria-console\n"
        "- Neptune Analytics graph: g-1xwjj34141 (Forgewing data)\n"
        "- GitHub App: vaultscaler-pr-gateway (each customer needs their own installation)\n"
        "- CI/CD: GitHub Actions workflows in aria-platform repo\n"
        "- Daemon: runs every ~90s, generates PRs, processes tasks\n\n"
        "When diagnosing issues:\n"
        "1. State what you observe from the data\n"
        "2. List possible causes ranked by likelihood\n"
        "3. Suggest specific actions (AWS CLI commands, code changes, manual steps)\n"
        "4. Flag blast radius (safe/moderate/dangerous) for each suggestion\n\n"
        "Be direct and specific. No hedging. If you don't have enough data, say "
        "exactly what additional data would help."
    )


def _invoke_bedrock(system_prompt: str, user_message: str) -> str:
    """Synchronous Bedrock invocation — wrap with asyncio.to_thread from async."""
    import boto3  # noqa: WPS433 — lazy

    client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    resp = client.invoke_model(
        modelId=OPS_CHAT_MODEL_ID,
        body=json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": OPS_CHAT_MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
            }
        ),
    )
    payload = json.loads(resp["body"].read())
    blocks = payload.get("content") or []
    for block in blocks:
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


# --- Infrastructure lockdown -----------------------------------------------
@router.get("/locks")
async def locks() -> dict[str, Any]:
    """Full infrastructure lock report — every check and every violation."""
    return infrastructure_lock.check_locks()


# --- Preemptive health -----------------------------------------------------
@router.get("/preemptive")
async def preemptive_alerts() -> dict[str, Any]:
    """All current preemptive alerts (real + honest stubs)."""
    alerts = preemptive.run_preemptive_checks()
    return {"count": len(alerts), "alerts": alerts}


# --- Support escalation ----------------------------------------------------
def _resolve_auto_heal_capability(decision: triage.TriageDecision) -> tuple[str, dict[str, Any]] | None:
    """
    Map a TriageDecision's `action` to a registered capability + kwargs,
    if and only if the decision is auto-approved AND we have a known
    mapping. Returns None to mean "escalate, don't auto-heal".
    """
    if not decision.auto_approved or decision.action == "noop":
        return None
    if decision.action == "restart_daemon_service":
        from nexus.config import FORGEWING_CLUSTER

        return ("restart_service", {"cluster": FORGEWING_CLUSTER, "service": "aria-daemon"})
    return None


@router.post("/support/escalate")
async def support_escalate(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """
    ARIA → Overwatch bridge for platform-level customer issues.

    Records the escalation, runs an immediate tenant health check,
    triages it, and either auto-heals (if a safe known-pattern action
    matches) or escalates to Ian via Telegram.
    """
    tenant_id = (payload or {}).get("tenant_id")
    issue = (payload or {}).get("issue", "")
    source = (payload or {}).get("source", "aria")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    overwatch_graph.record_event(
        event_type="support_escalation",
        service=f"tenant:{tenant_id}",
        details={"issue": issue, "source": source, "tenant_id": tenant_id},
        severity="warning",
    )

    health = tenant_health.check_tenant(tenant_id)
    decision = triage.triage_tenant_health(health)

    auto = _resolve_auto_heal_capability(decision)
    if auto is not None:
        capability_name, kwargs = auto
        try:
            action = registry.execute(capability_name, **kwargs)
            overwatch_graph.record_event(
                event_type="support_auto_healed",
                service=f"tenant:{tenant_id}",
                details={"capability": capability_name, "action_id": action.id},
                severity="info",
            )
            return {
                "status": "auto_healed",
                "tenant_id": tenant_id,
                "diagnosis": decision.reasoning,
                "action_taken": capability_name,
                "result": action.result,
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("auto-heal during support escalation failed")
            # Fall through to escalation below

    # Escalation path — fire a formatted Telegram via the registered capability
    try:
        registry.execute(
            "send_escalation",
            event=f"support_escalation:{tenant_id}",
            diagnosis=decision.reasoning,
            suggested_action=f"Investigate tenant {tenant_id}: {issue}",
        )
    except Exception:
        logger.exception("send_escalation capability call failed")

    return {
        "status": "escalated",
        "tenant_id": tenant_id,
        "diagnosis": decision.reasoning,
        "triage": decision.to_dict(),
        "message": "This has been escalated to our platform team. We're investigating.",
    }


@router.get("/support/escalations")
async def support_escalations(limit: int = 50) -> dict[str, Any]:
    """Recent support escalations recorded in the Overwatch graph."""
    rows = overwatch_graph.query(
        "MATCH (e:OverwatchPlatformEvent) WHERE e.event_type = 'support_escalation' "
        "RETURN e.id AS id, e.service AS service, e.details AS details, "
        "e.severity AS severity, e.created_at AS created_at "
        "ORDER BY e.created_at DESC LIMIT $lim",
        {"lim": limit},
    )
    if not rows:
        # Local mode — fall back to scanning the in-memory store
        rows = [
            e for e in overwatch_graph.get_recent_events(limit=200)
            if e.get("event_type") == "support_escalation"
        ][:limit]
    return {"count": len(rows), "escalations": rows}


@router.post("/ops/chat")
async def ops_chat(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Conversational platform-engineering assistant powered by Bedrock."""
    message = (payload or {}).get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    if MODE != "production":
        return {
            "response": (
                f"[Local mode] You asked: {message}\n\n"
                "In production this would call Bedrock with the full Overwatch "
                f"context (model={OPS_CHAT_MODEL_ID}). Set NEXUS_MODE=production "
                "to enable real responses."
            ),
            "model": OPS_CHAT_MODEL_ID,
            "mode": MODE,
        }

    try:
        status = await platform_status()
        tenants = await list_tenants()
        system_prompt = _build_ops_system_prompt(status, tenants)
        text = await asyncio.to_thread(_invoke_bedrock, system_prompt, message)
        return {"response": text or "(empty response)", "model": OPS_CHAT_MODEL_ID, "mode": MODE}
    except Exception as exc:  # noqa: BLE001
        logger.exception("ops_chat failed")
        return {"response": f"Bedrock call failed: {exc}", "error": True, "model": OPS_CHAT_MODEL_ID}

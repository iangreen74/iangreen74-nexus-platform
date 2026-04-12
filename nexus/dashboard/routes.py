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
from fastapi.responses import Response

from nexus import neptune_client, overwatch_graph
from nexus.capabilities import alert, ci_ops, daemon_ops, deploy_ops, ecs_ops, project_lifecycle, tenant_ops  # noqa: F401
from nexus.capabilities.registry import registry
from nexus.config import AWS_REGION, MODE, OPS_CHAT_MAX_TOKENS, OPS_CHAT_MODEL_ID
from nexus.forge import aria_repo, deploy_manager, fix_generator
from nexus.reasoning import triage
from nexus.reasoning.alert_dispatcher import maybe_alert
from nexus.reasoning.executor import execute_decision, execute_or_continue_chain, get_active_chain, get_all_active_chains
from nexus.reasoning.pattern_learner import (
    approve_candidate,
    capture_resolution,
    get_candidates,
    reject_candidate,
)
from nexus.sensors import (
    capability_discovery,
    capability_validator,
    ci_monitor,
    daemon_monitor,
    infrastructure_lock,
    preemptive,
    sre_metrics,
    tenant_health,
    tenant_validator,
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

    # Triage → Alert → Execute loop. For each sensor domain:
    #   1. Triage the health report to get a decision
    #   2. Fire Telegram alert (maybe_alert has 1h dedup)
    #   3. Execute the decision (executor has 30m cooldown + safety gates)
    # The executor is what makes Overwatch autonomous — it closes the
    # loop between "I see a problem" and "I fix it."
    executions: list[dict[str, Any]] = []

    daemon_decision = triage.triage_daemon_health(daemon)
    maybe_alert(
        "daemon", daemon_decision,
        dedup_key="daemon:" + daemon_decision.action,
        context={"running": daemon.get("running"), "stale": daemon.get("stale"),
                 "cycle_age_minutes": daemon.get("cycle_age_minutes"),
                 "heal_chain_active": get_active_chain("daemon") is not None},
    )
    daemon_exec = execute_or_continue_chain(
        daemon_decision,
        {"source": "daemon", "target": "aria-daemon"},
        sensor_healthy=daemon.get("healthy", False),
    )
    executions.append({"source": "daemon", "action": daemon_decision.action, **daemon_exec.to_dict()})

    ci_decision = triage.triage_ci_health(ci)
    maybe_alert(
        "ci", ci_decision,
        dedup_key="ci:" + ci_decision.action,
        context={"green_rate_24h": ci.get("green_rate_24h"),
                 "failing_workflows": ",".join(ci.get("failing_workflows", []) or []),
                 "heal_chain_active": get_active_chain("ci") is not None},
    )
    ci_exec = execute_or_continue_chain(
        ci_decision,
        {"source": "ci", "target": "ci"},
        sensor_healthy=ci.get("healthy", False),
    )
    executions.append({"source": "ci", "action": ci_decision.action, **ci_exec.to_dict()})

    for t in tenants:
        decision = triage.triage_tenant_health(t)
        tid = t.get("tenant_id", "unknown")
        maybe_alert(
            f"tenant:{tid}", decision,
            dedup_key=f"tenant:{tid}:{decision.action}",
            context={"overall_status": t.get("overall_status"),
                     "heal_chain_active": get_active_chain(f"tenant:{tid}") is not None},
        )
        t_exec = execute_or_continue_chain(
            decision,
            {"source": f"tenant:{tid}", "target": tid, "tenant_id": tid},
            sensor_healthy=(t.get("overall_status") == "healthy"),
        )
        executions.append({"source": f"tenant:{tid}", "action": decision.action, **t_exec.to_dict()})

        # Capability validation — triage blocked/degraded tenants
        try:
            cap_report = capability_validator.validate_tenant_capabilities(tid)
            if cap_report.overall in ("blocked", "degraded"):
                cap_decision = triage.triage_capability_report(cap_report.to_dict())
                maybe_alert(
                    f"capability:{tid}", cap_decision,
                    dedup_key=f"capability:{tid}:{cap_decision.action}",
                    context={"capability_overall": cap_report.overall,
                             "score": capability_validator.capability_score(cap_report),
                             "heal_chain_active": get_active_chain(f"capability:{tid}") is not None},
                )
                cap_exec = execute_or_continue_chain(
                    cap_decision,
                    {"source": f"capability:{tid}", "target": tid, "tenant_id": tid},
                    sensor_healthy=(cap_report.overall == "fully_operational"),
                )
                executions.append({"source": f"capability:{tid}", "action": cap_decision.action, **cap_exec.to_dict()})
        except Exception:
            logger.debug("capability validation failed for %s", tid, exc_info=True)

    # Performance drift detection — proactive alerts
    from nexus.sensors import performance
    from nexus.reasoning.triage import triage_performance_alert

    perf_daemon = performance.daemon_cycle_performance(hours=24)
    if perf_daemon.get("anomalous"):
        perf_decision = triage_performance_alert({
            "metric": "daemon_cycle_duration",
            "anomalous": True,
            "value": perf_daemon.get("latest"),
            "baseline_mean": (perf_daemon.get("stats") or {}).get("mean"),
            "trend": perf_daemon.get("trend"),
        })
        perf_exec = execute_or_continue_chain(
            perf_decision,
            {"source": "performance:daemon_cycle", "target": "aria-daemon"},
            sensor_healthy=not perf_daemon.get("anomalous", False),
        )
        executions.append({"source": "performance:daemon_cycle", "action": perf_decision.action, **perf_exec.to_dict()})

    for t in tenants:
        tid = t.get("tenant_id", "")
        if not tid:
            continue
        try:
            tv = performance.task_velocity(tid, hours=168)
            if tv.get("tasks_per_day", 0) == 0 and len(tv.get("daily_counts", [])) > 3:
                vel_decision = triage_performance_alert({
                    "metric": "task_velocity",
                    "tasks_per_day": 0,
                    "was_active": any(c > 0 for c in tv.get("daily_counts", [])),
                    "tenant_id": tid,
                })
                vel_exec = execute_or_continue_chain(
                    vel_decision,
                    {"source": f"performance:velocity:{tid}", "target": tid, "tenant_id": tid},
                    sensor_healthy=tv.get("tasks_per_day", 0) > 0,
                )
                executions.append({"source": f"performance:velocity:{tid}", "action": vel_decision.action, **vel_exec.to_dict()})

            ch = performance.context_health(tid)
            if ch.get("active", 8) < 4:
                ctx_decision = triage_performance_alert({
                    "metric": "context_health",
                    "active": ch.get("active"),
                    "expected": ch.get("expected"),
                    "missing": ch.get("missing"),
                    "tenant_id": tid,
                })
                ctx_exec = execute_or_continue_chain(
                    ctx_decision,
                    {"source": f"performance:context:{tid}", "target": tid, "tenant_id": tid},
                    sensor_healthy=ch.get("healthy", True),
                )
                executions.append({"source": f"performance:context:{tid}", "action": ctx_decision.action, **ctx_exec.to_dict()})
        except Exception:
            logger.debug("performance checks failed for %s", tid, exc_info=True)

    # Capability discovery — run full probe ~10% of cycles (~every 5 min)
    import random as _rnd
    if _rnd.random() < 0.1:
        capability_discovery.discover_capabilities(
            tenant_ids=[t.get("tenant_id") for t in tenants if t.get("tenant_id")]
        )
    forgewing_health = capability_discovery.get_capability_health()

    locks = infrastructure_lock.check_locks()
    preemptive_alerts = preemptive.run_preemptive_checks()
    if not locks.get("all_locked"):
        # Lock violations are always critical and override the rollup.
        overall = "degraded"

    # Execution stats
    executed_count = sum(1 for e in executions if e.get("status") == "executed")
    escalated_count = sum(1 for e in executions if e.get("status") == "escalated")

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
        "executions": executions,
        "execution_stats": {
            "executed": executed_count,
            "escalated": escalated_count,
            "total": len(executions),
        },
        "active_heal_chains": get_all_active_chains(),
        "performance": {
            "daemon_cycle": perf_daemon,
        },
        "forgewing_capabilities": forgewing_health,
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, Any]:
    reports = tenant_health.check_all_tenants()
    # Enrich each tenant with capability score + display name
    for report in reports:
        tid = report.get("tenant_id", "")
        ctx = report.get("context", {}) or {}
        report["display_name"] = ctx.get("name") or ctx.get("company_name") or tid
        try:
            cap_report = capability_validator.validate_tenant_capabilities(tid)
            report["capability_score"] = capability_validator.capability_score(cap_report)
            report["capability_overall"] = cap_report.overall
        except Exception:
            report["capability_score"] = "unknown"
            report["capability_overall"] = "unknown"
    return {"count": len(reports), "tenants": reports}


@router.get("/tenants/{tenant_id}")
async def tenant_detail(tenant_id: str) -> dict[str, Any]:
    report = tenant_health.check_tenant(tenant_id)
    decision = triage.triage_tenant_health(report)
    return {"report": report, "decision": decision.to_dict()}


@router.get("/tenants/{tenant_id}/detail")
async def tenant_full_detail(tenant_id: str) -> dict[str, Any]:
    """
    Full pipeline state for a single tenant: tasks, PRs, conversation,
    token validity, repo indexing status, validation alerts. This feeds
    the dashboard's expandable tenant rows and the Ops chat context.
    """
    ctx = neptune_client.get_tenant_context(tenant_id)
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=50)
    prs = neptune_client.get_recent_prs(tenant_id, limit=20)

    # Conversation — last 5 messages
    conversation_rows = neptune_client.query(
        "MATCH (cm:ConversationMessage {tenant_id: $tid}) "
        "RETURN cm.role AS role, cm.content AS content, "
        "cm.timestamp AS timestamp "
        "ORDER BY cm.timestamp DESC LIMIT 5",
        {"tid": tenant_id},
    )
    # Reverse so they're chronological
    conversation_rows = list(reversed(conversation_rows))

    # Token validity
    token_info = tenant_health._check_token(tenant_id)

    # Repo indexing
    files = neptune_client.query(
        "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
        {"tid": tenant_id},
    )
    repo_file_count = int(files[0].get("c", 0)) if files else 0

    # Ingestion run
    ingest = neptune_client.query(
        "MATCH (r:IngestRun {tenant_id: $tid}) "
        "RETURN r.status AS status, r.files_indexed AS files_indexed, "
        "r.started_at AS started_at, r.completed_at AS completed_at "
        "ORDER BY r.started_at DESC LIMIT 1",
        {"tid": tenant_id},
    )

    # Validation alerts
    validation = tenant_validator.validate_tenant(tenant_id)

    # Capability validation (full 8-layer check)
    cap_report = capability_validator.validate_tenant_capabilities(tenant_id)

    # Triage decision
    report = tenant_health.check_tenant(tenant_id)
    decision = triage.triage_tenant_health(report)

    return {
        "tenant_id": tenant_id,
        "context": ctx,
        "mission_stage": ctx.get("mission_stage", "unknown"),
        "pipeline_stage": report.get("pipeline_stage", "unknown"),
        "pipeline_summary": report.get("pipeline_summary", ""),
        "tasks": tasks,
        "task_count": len(tasks),
        "prs": prs,
        "pr_count": len(prs),
        "conversation": conversation_rows,
        "conversation_count": neptune_client.get_conversation_count(tenant_id),
        "token": token_info,
        "repo_file_count": repo_file_count,
        "ingestion": ingest[0] if ingest else None,
        "capabilities": cap_report.to_dict(),
        "validation": {
            "alert_count": len(validation),
            "alerts": validation,
        },
        "triage": decision.to_dict(),
        "health": report,
    }


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
    """Build a comprehensive diagnostic — one paste gives full context."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: list[str] = [f"OVERWATCH DIAGNOSTIC — {now}"]
    lines.append(f"Platform: {status.get('overall', 'unknown').upper()}")
    lines.append("")

    # --- Section 1: Platform overview ---
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

    # CI enrichment from S3 (real-time, richer than GitHub API)
    try:
        from nexus.ci_reader import get_ci_health_summary, get_deploy_outcome_summary

        s3_ci = get_ci_health_summary()
        if s3_ci.get("status") != "unavailable":
            lines.append(
                f"CI (S3): {s3_ci.get('status', '?')} — "
                f"{s3_ci.get('total_tests', 0)} tests, "
                f"{s3_ci.get('failed_count', 0)} failed — "
                f"commit {s3_ci.get('commit_sha', '?')[:8]}"
            )
        s3_deploy = get_deploy_outcome_summary()
        if s3_deploy.get("status") != "unavailable":
            lines.append(
                f"Last deploy (S3): {s3_deploy.get('service', '?')} → "
                f"{s3_deploy.get('status', '?')} — "
                f"commit {s3_deploy.get('commit_sha', '?')[:8]}"
            )
    except Exception:
        pass

    ts = status.get("tenants", {}) or {}
    lines.append(
        f"Tenants: {status.get('tenant_count', 0)} total — "
        f"{ts.get('healthy', 0)} healthy, {ts.get('degraded', 0)} degraded, "
        f"{ts.get('critical', 0)} critical, {ts.get('pending', 0)} pending"
    )
    lines.append("")

    # --- Section 2: Enhanced tenant health ---
    for t in tenants.get("tenants", []):
        tid = t.get("tenant_id", "unknown")
        display = t.get("display_name", tid)
        lines.append(f"TENANT {display} ({tid}): {(t.get('overall_status') or 'unknown').upper()}")
        ctx = t.get("context", {}) or {}
        lines.append(f"  Stage: {ctx.get('mission_stage', '—')} | Pipeline: {t.get('pipeline_summary', '—')}")
        deployment = t.get("deployment", {}) or {}
        if deployment.get("provisioned") is False:
            lines.append(f"  Deploy: not provisioned | Reason: {deployment.get('reason', 'no infra in Neptune')}")
        else:
            stack = deployment.get("stack") or {}
            url = stack.get("url") or stack.get("stack_name") or "—"
            lines.append(f"  Deploy: {url} | Status: {stack.get('status', '—')}")
        pipeline = t.get("pipeline", {}) or {}
        lines.append(
            f"  Tasks: {pipeline.get('total_recent_tasks', 0)} total, "
            f"{pipeline.get('tasks_in_progress', 0)} in progress, "
            f"{pipeline.get('tasks_pending', 0)} pending, "
            f"{pipeline.get('stuck_task_count', 0)} stuck"
        )
        lines.append(f"  PRs: {pipeline.get('pr_count', 0)} | Last PR: {pipeline.get('last_pr_at', '—')}")
        conv = t.get("conversation", {}) or {}
        lines.append(f"  Messages: {conv.get('message_count', 0)} | Last: {conv.get('last_message_at', '—')}")
        token = t.get("token", {}) or {}
        lines.append(f"  Token: {'present' if token.get('present') else 'EMPTY'} | Installation: {token.get('installation_id', '—')}")
        deploy_stuck = t.get("deploy_stuck", False)
        if deploy_stuck:
            lines.append(f"  ⚠ DEPLOY STUCK at stage: {t.get('deploy_stage', '—')}")
        feat = t.get("deploy_features", {}) or {}
        if feat:
            parts = []
            if feat.get("preview_available"):
                parts.append("preview ✓")
            if feat.get("smoke_test_available"):
                rate = feat.get("smoke_pass_rate")
                parts.append(f"smoke {rate}%" if rate is not None else "smoke ✓")
            if parts:
                lines.append(f"  Features: {' · '.join(parts)}")
        # Project lifecycle — per-project breakdown
        try:
            from nexus.capabilities.project_lifecycle import get_project_lifecycle
            from nexus.capabilities.forgewing_api import call_api as _fw

            lc = get_project_lifecycle(tenant_id=tid)
            proj = lc.get("active_project")
            archived_count = lc.get("archived_count", 0)
            lines.append(f"  Projects: {1 if proj else 0} active, {archived_count} archived")
            if proj:
                lines.append(f"    ● {proj.get('name', '—')} (active) | Repo: {proj.get('repo_url', '—')}")
            # Try to get full project list from Forgewing API
            projects_resp = _fw("GET", f"/projects/{tid}")
            if not projects_resp.get("error") and isinstance(projects_resp.get("projects"), list):
                for p in projects_resp["projects"]:
                    p_status = p.get("status", "unknown")
                    name = p.get("name") or p.get("repo_url", "?")
                    marker = "●" if p_status == "active" else "○"
                    extra = ""
                    if p.get("task_count"):
                        extra += f" | {p['task_count']} tasks"
                    if p.get("pr_count"):
                        extra += f", {p['pr_count']} PRs"
                    lines.append(f"    {marker} {name} ({p_status}){extra}")
            if lc.get("pending_restart"):
                lines.append("    ⚠ pending_restart flag set")
            if lc.get("last_event"):
                ev = lc["last_event"]
                lines.append(f"    Last lifecycle: {ev.get('type', '?')} at {ev.get('at', '?')}")
        except Exception:
            pass
        lines.append("")

    # --- Ground truth + velocity ---
    try:
        from nexus.sensors.ground_truth import get_tenant_ground_truth

        lines.append("GROUND TRUTH (live checks):")
        for t in tenants.get("tenants", []):
            tid = t.get("tenant_id", "unknown")
            gt = get_tenant_ground_truth(tid)
            dep = gt.get("deploy", {})
            prs = gt.get("prs", {})
            tasks = gt.get("tasks", {})
            vel = gt.get("velocity", {})
            url_str = dep.get("app_url") or "no URL"
            ds = dep.get("deploy_status", "—")
            if ds == "not_started":
                http_str = "not deployed"
            elif dep.get("http_status"):
                http_str = f"HTTP {dep['http_status']}"
            else:
                http_str = ds
            lines.append(f"  {tid}:")
            lines.append(f"    App: {url_str} → {http_str}")
            lines.append(f"    PRs: {prs.get('total', 0)} ({prs.get('merged', 0)} merged, {prs.get('pending', 0)} pending)")
            lines.append(f"    Tasks: {tasks.get('total', 0)} ({tasks.get('complete', 0)} complete, {tasks.get('pending', 0)} pending)")
            if vel.get("avg_pr_cycle_minutes"):
                lines.append(f"    Avg PR cycle: {vel['avg_pr_cycle_minutes']}m | Last PR: {vel.get('last_pr_age_hours', '—')}h ago")
            lines.append(f"    Completion rate: {vel.get('completion_rate', 0)}%")
        lines.append("")
    except Exception:
        lines.append("GROUND TRUTH: unavailable")
        lines.append("")

    # --- Section 3: Active heal chains ---
    try:
        chains = status.get("active_heal_chains", {}) or {}
        if chains:
            lines.append("ACTIVE HEAL CHAINS:")
            for source, c in chains.items():
                if not isinstance(c, dict):
                    continue
                results = c.get("step_results", []) or []
                wait = (
                    f"waiting {c.get('cycles_waited', 0)}/{c.get('cycles_to_wait', 0)}"
                    if c.get("awaiting_verification") else "ready"
                )
                lines.append(f"  {source} → {c.get('chain', '?')}")
                lines.append(
                    f"    Step {c.get('step', 0)}, attempts {c.get('total_attempts', 0)}, {wait}"
                )
                for s in results:
                    cap = s.get("capability", "?")
                    res = s.get("result", "?")
                    detail = s.get("detail")
                    lines.append(f"    [{s.get('step', '?')}] {cap} → {res}")
                    if detail:
                        # detail is often a stringified dict; truncate for readability
                        detail_str = str(detail).replace("\n", " ")
                        if len(detail_str) > 300:
                            detail_str = detail_str[:300] + "..."
                        lines.append(f"        {detail_str}")
        else:
            lines.append("ACTIVE HEAL CHAINS: none")
    except Exception as exc:
        lines.append(f"ACTIVE HEAL CHAINS: unavailable ({str(exc)[:80]})")
    lines.append("")

    # --- Section 4: Triage decisions ---
    execs = status.get("executions", [])
    if execs:
        lines.append(f"TRIAGE DECISIONS ({len(execs)} this cycle):")
        for e in execs:
            src = e.get("source", "?")
            act = e.get("action", "?")
            st = e.get("status", "?")
            reason = e.get("reason") or e.get("outcome") or ""
            taken = e.get("action_taken", "")
            lines.append(f"  {src}: {act} → {st.upper()} {taken or reason}")
    lines.append("")

    # --- Section 5: Forgewing capabilities ---
    fw = status.get("forgewing_capabilities", {})
    if fw.get("total"):
        lines.append(f"FORGEWING CAPABILITIES: {fw.get('available', 0)}/{fw['total']} available (avg {fw.get('avg_response_ms', 0)}ms)")
    else:
        lines.append("FORGEWING CAPABILITIES: not yet discovered")

    # --- Section 6: Infrastructure locks ---
    infra = status.get("infrastructure", {})
    if infra.get("all_locked"):
        lines.append("INFRASTRUCTURE: ALL LOCKED ✓")
    else:
        lines.append(f"INFRASTRUCTURE: {infra.get('violation_count', 0)} VIOLATIONS")
        for v in infra.get("violations", [])[:5]:
            lines.append(f"  ⚠ {v.get('lock', '?')}: {v.get('reason', '?')}")
    lines.append("")

    # --- Section 7: Patterns ---
    if patterns:
        lines.append("LEARNED FAILURE PATTERNS:")
        for p in patterns[:10]:
            lines.append(
                f"  - {p.get('name')}: {p.get('occurrence_count', 0)}x, "
                f"confidence={(p.get('confidence', 0) or 0) * 100:.0f}%, "
                f"blast={p.get('blast_radius', '—')}"
            )
        lines.append("")

    # --- Section 8: Recent actions with context ---
    if actions:
        lines.append("RECENT ACTIONS:")
        for a in actions[:15]:
            outcome = "OK" if a.get("ok") else (a.get("error") or "FAILED")
            kwargs = a.get("kwargs", {}) or {}
            target = kwargs.get("tenant_id") or kwargs.get("service") or kwargs.get("cluster") or ""
            hint = f" [{target}]" if target else ""
            lines.append(f"  {a.get('started_at', '')[:19]}  {a.get('name', '')}{hint}  →  {outcome}")
        lines.append("")

    # --- Section 9: Graph stats ---
    try:
        stats = overwatch_graph.graph_stats()
        total = sum(stats.values())
        parts = [f"{k.replace('Overwatch', '')}:{v}" for k, v in stats.items() if v > 0]
        lines.append(f"GRAPH: {total} nodes — {' · '.join(parts)}")
    except Exception:
        pass
    lines.append("")

    # --- Section 10: Intelligence status (per tenant) ---
    try:
        lines.append("INTELLIGENCE STATUS:")
        for t in tenants.get("tenants", []):
            tid = t.get("tenant_id", "unknown")
            conv = t.get("conversation", {}) or {}
            msg_count = conv.get("message_count", 0)
            profile = "built" if msg_count >= 10 else "not built"
            profile_detail = f"{msg_count} messages" if msg_count > 0 else "no conversations"
            lines.append(f"  {tid}:")
            lines.append(f"    User profile: {profile} ({profile_detail})")
        lines.append("")
    except Exception:
        pass

    # --- Section 11: CI/CD credential status ---
    try:
        lines.append("CI/CD CREDENTIALS:")
        for t in tenants.get("tenants", []):
            tid = t.get("tenant_id", "unknown")
            deployment = t.get("deployment", {}) or {}
            token = t.get("token", {}) or {}
            has_deploy = deployment.get("provisioned", False)
            has_token = token.get("present", False)
            if has_deploy:
                tier = "Tier 3 (CodeBuild)" if deployment.get("stack") else "Tier 1/2"
                lines.append(f"  {tid}: deploy={tier}, token={'present' if has_token else 'EMPTY'}")
            else:
                lines.append(f"  {tid}: not provisioned, token={'present' if has_token else 'EMPTY'}")
        lines.append("")
    except Exception:
        pass

    # --- Section 12: Engineering insights ---
    try:
        from nexus.engineering_patterns import get_recommendations

        recs = get_recommendations(limit=3)
        if recs:
            lines.append("ENGINEERING INSIGHTS:")
            for r in recs:
                lines.append(f"  - [{r['type']}] {r['insight']} ({r.get('data_points', 0)} data points)")
            lines.append("")
    except Exception:
        pass

    # --- Section 13: Proactive alerts ---
    try:
        from nexus.proactive_scanner import get_all_suggestions_summary

        summary = get_all_suggestions_summary()
        total = summary.get("total", 0)
        lines.append(f"PROACTIVE ALERTS: {total} pending across {summary.get('tenants_with_suggestions', 0)} tenants")
        if total > 0:
            for cat, count in summary.get("by_category", {}).items():
                lines.append(f"  - {cat}: {count}")
        lines.append("")
    except Exception:
        pass

    # --- Section 14: Runner health ---
    try:
        from nexus.runner_health import check_all_runners, format_for_report

        runner_results = check_all_runners()
        lines.append(format_for_report(runner_results))
        lines.append("")
    except Exception:
        pass

    # --- Section 15: Synthetic tests ---
    try:
        from nexus.synthetic_tests import get_summary

        st = get_summary()
        score = f"{st['passed']}/{st['total']} ({st['score_pct']}%)"
        lines.append(f"SYNTHETIC TESTS: {score}")
        for r in st.get("results", []):
            marker = "pass" if r["status"] == "pass" else r["status"].upper()
            ms = f" ({r.get('duration_ms', 0)}ms)" if r.get("duration_ms") else ""
            detail = f" — {r['details']}" if r.get("details") else ""
            err = f" — {r['error']}" if r.get("error") else ""
            lines.append(f"  {marker}: {r['name']}{ms}{detail}{err}")
        lines.append("")
    except Exception:
        pass

    # --- Section 15b: Deploy errors (failed DeploymentProgress nodes) ---
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (d:DeploymentProgress) "
            "WHERE d.stage IN ['failed', 'error'] "
            "RETURN d.tenant_id AS tid, d.stage AS stage, "
            "d.message AS msg, d.error AS err, "
            "d.codebuild_phase AS phase, d.updated_at AS updated "
            "ORDER BY d.updated_at DESC LIMIT 10",
            {},
        )
        if rows:
            lines.append("DEPLOY ERRORS:")
            for r in rows:
                tid = r.get("tid", "?")
                stage = r.get("stage", "?")
                msg = (r.get("msg") or r.get("err") or "").strip()
                phase = r.get("phase") or ""
                updated = (r.get("updated") or "")[:19]
                phase_str = f" phase={phase}" if phase else ""
                lines.append(f"  {tid}: {stage}{phase_str} @ {updated}")
                if msg:
                    lines.append(f"    {msg[:250]}")
            lines.append("")
    except Exception as exc:
        lines.append(f"DEPLOY ERRORS: unavailable ({str(exc)[:80]})")
        lines.append("")

    # --- Section 15c: Project isolation ---
    try:
        from nexus.capabilities.project_lifecycle import get_project_lifecycle

        legacy_tenants: list[str] = []
        clean_count = 0
        for t in tenants.get("tenants", []) or []:
            tid = t.get("tenant_id", "")
            if not tid:
                continue
            lc = get_project_lifecycle(tenant_id=tid) or {}
            proj = lc.get("active_project") or {}
            pid = proj.get("id") or proj.get("project_id") or ""
            if pid and pid == tid:
                legacy_tenants.append(tid)
            elif pid and pid.startswith("proj-"):
                clean_count += 1
        total = clean_count + len(legacy_tenants)
        if total:
            lines.append(
                f"PROJECT ISOLATION: {clean_count}/{total} on proj-* IDs, "
                f"{len(legacy_tenants)} on legacy default project"
            )
            for tid in legacy_tenants:
                lines.append(f"  ⚠ {tid}: active_project.id == tenant_id (legacy)")
            lines.append("")
    except Exception as exc:
        lines.append(f"PROJECT ISOLATION: unavailable ({str(exc)[:80]})")
        lines.append("")

    # --- Section 16: Code health (Capability 30) ---
    try:
        from nexus.nexus_code_auditor import format_report_text, get_latest_report

        audit = get_latest_report()
        if audit:
            lines.append("CODE HEALTH:")
            lines.append(
                f"  Score: {audit.get('health_score', '?')}/100 — "
                f"{audit.get('total_findings', 0)} findings "
                f"(critical={audit.get('critical', 0)}, high={audit.get('high', 0)}, "
                f"medium={audit.get('medium', 0)}, low={audit.get('low', 0)})"
            )
            for sev in ("critical", "high"):
                items = [f for f in audit.get("findings", []) if f.get("severity") == sev][:5]
                for f in items:
                    lines.append(
                        f"  [{sev.upper()}][{f.get('rule', '?')}] "
                        f"{f.get('file', '?')}:{f.get('line', '?')} — {f.get('message', '')}"
                    )
            lines.append("")
    except Exception as exc:
        lines.append(f"CODE HEALTH: unavailable ({str(exc)[:80]})")
        lines.append("")

    lines.append("---")
    lines.append("Paste this into Claude with: 'Here is the latest Overwatch report.'")
    return "\n".join(lines)


def _format_tenant_report(tenant_id: str) -> str:
    """Focused diagnostic for a single tenant."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [f"TENANT REPORT — {tenant_id}", f"Generated: {now}", ""]

    # Full detail from the tenant detail endpoint
    ctx = neptune_client.get_tenant_context(tenant_id)
    lines.append(f"Name: {ctx.get('name') or ctx.get('company_name') or tenant_id}")
    lines.append(f"Mission stage: {ctx.get('mission_stage', '—')}")
    lines.append(f"Repo: {ctx.get('repo_url', '—')}")
    lines.append("")

    # Tasks
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=20)
    by_status: dict[str, int] = {}
    for t in tasks:
        s = t.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    lines.append(f"TASKS ({len(tasks)} total):")
    for s, c in sorted(by_status.items()):
        lines.append(f"  {s}: {c}")
    for t in tasks[:10]:
        lines.append(f"  [{t.get('status', '?'):12}] {(t.get('description') or t.get('id') or '?')[:70]}")
    lines.append("")

    # PRs
    prs = neptune_client.get_recent_prs(tenant_id, limit=10)
    lines.append(f"PRS ({len(prs)}):")
    for p in prs[:5]:
        lines.append(f"  [{p.get('state', '?'):8}] {p.get('pr_url', '—')}")
    lines.append("")

    # Token
    from nexus.sensors.tenant_health import _check_token
    token = _check_token(tenant_id)
    lines.append(f"Token: {'present' if token.get('present') else 'EMPTY'} | Installation: {token.get('installation_id', '—')}")

    # Repo files
    files = neptune_client.query(
        "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
        {"tid": tenant_id},
    )
    file_count = int(files[0].get("c", 0)) if files else 0
    lines.append(f"Repo files indexed: {file_count}")

    # Conversation
    conv_count = neptune_client.get_conversation_count(tenant_id)
    lines.append(f"Conversation messages: {conv_count}")
    lines.append("")

    # Deploy state
    dp = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) RETURN d.stage AS stage, d.message AS msg",
        {"tid": tenant_id},
    )
    if dp:
        lines.append(f"Deploy progress: stage={dp[0].get('stage')} msg={dp[0].get('msg', '')[:80]}")
    else:
        lines.append("Deploy progress: none (no DeploymentProgress node)")

    # Validation
    alerts = tenant_validator.validate_tenant(tenant_id)
    if alerts:
        lines.append(f"\nVALIDATION ALERTS ({len(alerts)}):")
        for a in alerts:
            lines.append(f"  [{a.get('severity', '?')}] {a.get('check', '?')}: {a.get('message', '')[:80]}")

    # Health + triage
    report = tenant_health.check_tenant(tenant_id)
    decision = triage.triage_tenant_health(report)
    lines.append(f"\nTriage: {decision.action} (confidence={decision.confidence:.0%}, blast={decision.blast_radius})")
    lines.append(f"  {decision.reasoning}")

    lines.append("")
    lines.append("---")
    lines.append("Paste this into Claude with: 'Diagnose this tenant.'")
    return "\n".join(lines)


@router.get("/validate/tenants")
async def validate_tenants() -> dict[str, Any]:
    """Proactive tenant validation — all checks for all tenants."""
    results = tenant_validator.validate_all_tenants()
    total_alerts = sum(len(a) for a in results.values())
    return {
        "tenant_count": len(results),
        "total_alerts": total_alerts,
        "tenants": {
            tid: {"alert_count": len(alerts), "alerts": alerts}
            for tid, alerts in results.items()
        },
    }


# --- SRE Metrics -----------------------------------------------------------
@router.get("/sre")
async def sre_dashboard() -> dict[str, Any]:
    """Full SRE metrics dashboard with trends and antifragile score."""
    return sre_metrics.get_sre_dashboard()


@router.get("/sre/incidents")
async def sre_incidents() -> dict[str, Any]:
    """Recent incidents with lifecycle timestamps."""
    open_inc = overwatch_graph.get_open_incidents()
    resolved = overwatch_graph.get_resolved_incidents(hours=168)
    return {
        "open": open_inc,
        "open_count": len(open_inc),
        "resolved": resolved,
        "resolved_count": len(resolved),
    }


@router.get("/discovery")
async def api_discovery() -> dict[str, Any]:
    """Discover Forgewing capabilities and their health."""
    tenant_ids = neptune_client.get_tenant_ids()
    return capability_discovery.discover_capabilities(tenant_ids)


@router.get("/heal-chains")
async def heal_chains() -> dict[str, Any]:
    """Active heal chains and their progress."""
    from nexus.reasoning.heal_chain import CHAINS

    chains = get_all_active_chains()
    available = {name: {"steps": len(c.steps), "pattern": c.pattern_name} for name, c in CHAINS.items()}
    return {
        "active": chains,
        "active_count": len(chains),
        "available_chains": available,
        "available_count": len(available),
    }


# --- Pattern Learning (Level 4) -------------------------------------------

@router.get("/patterns/candidates")
async def pattern_candidates() -> dict[str, Any]:
    """List all candidate patterns awaiting graduation."""
    candidates = get_candidates()
    return {
        "candidates": [c.to_dict() for c in candidates],
        "count": len(candidates),
        "graduated": sum(1 for c in candidates if c.graduated),
        "pending": sum(1 for c in candidates if not c.graduated),
    }


@router.post("/patterns/capture-resolution")
async def api_capture_resolution(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Capture a manual resolution and generate a candidate pattern."""
    required = ["incident_source", "incident_action", "heal_capability", "root_cause", "resolution"]
    missing = [k for k in required if not body.get(k)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")
    cp = capture_resolution(
        incident_source=body["incident_source"],
        incident_action=body["incident_action"],
        heal_capability=body["heal_capability"],
        root_cause=body["root_cause"],
        resolution_text=body["resolution"],
        should_auto_heal=body.get("should_auto_heal", False),
        blast_radius=body.get("blast_radius", "safe"),
        heal_kwargs_template=body.get("heal_kwargs_template", {}),
    )
    return {"candidate": cp.to_dict(), "status": "created"}


@router.post("/patterns/candidates/{name}/approve")
async def api_approve_candidate(name: str) -> dict[str, Any]:
    """Approve a candidate pattern — its suggested heal worked."""
    cp = approve_candidate(name)
    if not cp:
        raise HTTPException(status_code=404, detail=f"Candidate '{name}' not found")
    return {"candidate": cp.to_dict(), "graduated": cp.graduated, "status": "approved"}


@router.post("/patterns/candidates/{name}/reject")
async def api_reject_candidate(name: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Reject a candidate pattern — its suggested heal didn't work."""
    cp = reject_candidate(name, reason=(body or {}).get("reason", ""))
    if not cp:
        raise HTTPException(status_code=404, detail=f"Candidate '{name}' not found")
    return {"candidate": cp.to_dict(), "status": "rejected"}


@router.post("/patterns/reload")
async def reload_patterns() -> dict[str, Any]:
    """Reload graduated patterns into triage. Call after manual graduation."""
    from nexus.reasoning.triage import _load_graduated_into_known

    count = _load_graduated_into_known()
    return {"reloaded": count, "status": "ok"}


async def _build_full_report() -> str:
    """Generate the diagnostic text. Never raises — degrades to partial report."""
    sections: dict[str, Any] = {
        "status": {},
        "tenants": {"tenants": []},
        "actions": [],
        "patterns": [],
    }
    try:
        sections["status"] = await platform_status()
    except Exception as exc:
        logger.warning("platform_status failed in report: %s", exc)
        sections["status"] = {"overall": "unknown", "_error": str(exc)[:200]}
    try:
        sections["tenants"] = await list_tenants()
    except Exception as exc:
        logger.warning("list_tenants failed in report: %s", exc)
    try:
        sections["actions"] = (await actions(limit=15)).get("actions", [])
    except Exception as exc:
        logger.warning("actions failed in report: %s", exc)
    try:
        sections["patterns"] = (await failure_patterns(min_confidence=0.0)).get("patterns", [])
    except Exception as exc:
        logger.warning("patterns failed in report: %s", exc)
    try:
        return _format_report(**sections)
    except Exception as exc:
        logger.exception("_format_report failed")
        return (
            f"OVERWATCH DIAGNOSTIC — partial\n"
            f"Report generation failed: {exc}\n\n"
            f"Raw status keys: {list(sections['status'].keys())}\n"
            f"Tenants: {len(sections['tenants'].get('tenants', []))}\n"
            f"Actions: {len(sections['actions'])}\n"
            f"Patterns: {len(sections['patterns'])}\n"
        )


@router.get("/diagnostic-report")
async def diagnostic_report() -> dict[str, Any]:
    """Generate a structured text diagnostic for pasting into Claude."""
    text = await _build_full_report()
    return {"report": text, "generated_at": datetime.now(timezone.utc).isoformat()}


@router.get("/download-report")
async def download_report() -> Response:
    """Download the full diagnostic report as a .md file."""
    try:
        text = await _build_full_report()
    except Exception as exc:
        text = f"# Report Generation Failed\n\nError: {exc}\n"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    filename = f"overwatch-report-{timestamp}.md"
    return Response(
        content=text,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tenant-report/{tenant_id}")
async def tenant_report(tenant_id: str) -> dict[str, Any]:
    """Focused diagnostic report for a single tenant."""
    text = _format_tenant_report(tenant_id)
    return {"report": text, "tenant_id": tenant_id, "generated_at": datetime.now(timezone.utc).isoformat()}


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
async def ops_chat_endpoint(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Ops Chat — ask questions, get answers, execute actions."""
    from nexus.dashboard.ops_chat import chat as _ops_chat

    message = (payload or {}).get("message", "").strip()
    history = (payload or {}).get("history", [])
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    # Gather rich context from all sensors
    try:
        status_data = await platform_status()
        tenants_data = await list_tenants()
        patterns_data = await failure_patterns(min_confidence=0.0)
    except Exception:
        status_data, tenants_data, patterns_data = {}, {"tenants": []}, {"patterns": []}

    # Engineering insights for proactive recommendations
    try:
        from nexus.engineering_patterns import get_recommendations

        eng_insights = get_recommendations(limit=3)
    except Exception:
        eng_insights = []

    context = {
        "status": status_data,
        "tenants": tenants_data.get("tenants", []),
        "heal_chains": status_data.get("active_heal_chains", {}),
        "executions": status_data.get("executions", []),
        "patterns": patterns_data.get("patterns", []),
        "engineering_insights": eng_insights,
        "capabilities": [
            {"name": c.name, "description": c.description, "blast_radius": c.blast_radius}
            for c in registry.list_all()
        ],
    }

    result = await asyncio.to_thread(_ops_chat, message, context, history)
    result["mode"] = MODE
    result["model"] = OPS_CHAT_MODEL_ID
    return result


# --- AIOps: Deploy Decision + Outcome + CI Reader ----------------------------


@router.post("/deploy-decision")
async def deploy_decision(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """CI calls this before deploying. Returns DEPLOY / HOLD / CANARY."""
    from nexus.deploy_decision import evaluate_deploy_request

    required = ("commit_sha", "service")
    for field in required:
        if not (payload or {}).get(field):
            raise HTTPException(status_code=400, detail=f"{field} is required")

    result = evaluate_deploy_request(payload)
    overwatch_graph.record_event(
        event_type="deploy_decision",
        service=payload.get("service", ""),
        severity="info" if result["decision"] == "DEPLOY" else "warning",
        details={
            "decision": result["decision"],
            "reason": result["reason"],
            "commit_sha": payload.get("commit_sha", ""),
            "risk_score": payload.get("risk_score"),
        },
    )
    return result


@router.post("/deploy-outcome")
async def deploy_outcome(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """CI reports deploy outcome after completion. Feeds pattern learning."""
    from nexus.deploy_patterns import record_deploy_outcome

    if not (payload or {}).get("commit_sha"):
        raise HTTPException(status_code=400, detail="commit_sha is required")
    if not (payload or {}).get("status"):
        raise HTTPException(status_code=400, detail="status is required")

    node_id = record_deploy_outcome(payload)
    return {"recorded": True, "event_id": node_id}


@router.get("/ci/s3")
async def ci_from_s3() -> dict[str, Any]:
    """Latest CI result read from S3 (faster than GitHub API polling)."""
    from nexus.ci_reader import get_ci_health_summary, get_deploy_outcome_summary

    return {
        "ci": get_ci_health_summary(),
        "last_deploy": get_deploy_outcome_summary(),
    }


@router.get("/deploy-patterns")
async def deploy_pattern_stats() -> dict[str, Any]:
    """Deploy success rate and recent failure count."""
    from nexus.deploy_patterns import get_deploy_success_rate, get_deploy_failure_count

    return {
        "success_rate_24h": get_deploy_success_rate(hours=24),
        "failures_6h": get_deploy_failure_count(hours=6),
    }


@router.get("/engineering-insights")
async def engineering_insights() -> dict[str, Any]:
    """Cross-tenant engineering patterns and recommendations."""
    from nexus.engineering_patterns import analyze_all, get_recommendations

    return {
        "patterns": [p for p in analyze_all() if p],
        "recommendations": get_recommendations(limit=5),
    }


@router.get("/proactive-suggestions")
async def proactive_suggestions() -> dict[str, Any]:
    """Proactive suggestion summary across all tenants."""
    from nexus.proactive_scanner import get_all_suggestions_summary

    return get_all_suggestions_summary()


@router.get("/proactive-suggestions/{tenant_id}")
async def tenant_suggestions(tenant_id: str) -> dict[str, Any]:
    """Proactive suggestions for a specific tenant."""
    from nexus.proactive_scanner import get_suggestions

    suggestions = get_suggestions(tenant_id)
    return {"tenant_id": tenant_id, "suggestions": suggestions}


@router.post("/proactive-scan")
async def trigger_proactive_scan() -> dict[str, Any]:
    """Trigger a proactive scan across all tenants (on-demand)."""
    from nexus.proactive_scanner import scan_all_tenants

    results = scan_all_tenants()
    total = sum(len(v) for v in results.values())
    return {
        "scanned": len(results),
        "suggestions": total,
        "by_tenant": {tid: len(s) for tid, s in results.items()},
    }


@router.post("/synthetic-tests")
async def trigger_synthetic_tests() -> dict[str, Any]:
    """Run synthetic user journey tests on-demand."""
    from nexus.synthetic_tests import run_all_journeys

    results = run_all_journeys(force=True)
    passed = sum(1 for r in results if r["status"] == "pass")
    return {"results": results, "passed": passed, "total": len(results)}


@router.get("/synthetic-tests")
async def get_synthetic_results() -> dict[str, Any]:
    """Get cached synthetic test results."""
    from nexus.synthetic_tests import get_summary

    return get_summary()


@router.post("/synthetic-tests/remediate")
async def trigger_remediation() -> dict[str, Any]:
    """Run synthetic tests + attempt auto-remediation on failures."""
    from nexus.auto_remediation import run_and_remediate

    return run_and_remediate()


@router.get("/runners")
async def runner_health_summary() -> dict[str, Any]:
    """Runner health summary — disk, docker, agent, socket per runner."""
    from nexus.runner_health import get_summary

    return get_summary()


@router.post("/runners/check")
async def trigger_runner_check() -> dict[str, Any]:
    """Force a fresh runner health check (bypasses cache)."""
    from nexus.runner_health import check_all_runners

    results = check_all_runners(force=True)
    return {"runners": results, "total": len(results)}


@router.post("/code-audit")
async def trigger_code_audit(payload: dict[str, Any] = Body(default=None)) -> dict[str, Any]:
    """Run the full code audit against aria-platform (clones repo)."""
    from nexus.nexus_code_auditor import run_audit

    body = payload or {}
    local_path = body.get("local_path")
    repo_url = body.get("repo_url")
    return await asyncio.to_thread(
        run_audit, repo_url=repo_url, local_path=local_path, store_results=True
    )


@router.get("/code-audit")
async def latest_code_audit() -> dict[str, Any]:
    """Return the most recent code audit report from the graph."""
    from nexus.nexus_code_auditor import get_latest_report

    report = get_latest_report()
    if not report:
        return {"status": "no_audit_yet", "message": "No audit has been run yet"}
    return report


@router.get("/code-audit/text")
async def latest_code_audit_text() -> dict[str, Any]:
    """Return the latest audit report as plain-text (for copy/paste)."""
    from nexus.nexus_code_auditor import format_report_text, get_latest_report

    return {"report": format_report_text(get_latest_report())}

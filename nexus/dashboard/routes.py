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
                    status = p.get("status", "unknown")
                    name = p.get("name") or p.get("repo_url", "?")
                    marker = "●" if status == "active" else "○"
                    extra = ""
                    if p.get("task_count"):
                        extra += f" | {p['task_count']} tasks"
                    if p.get("pr_count"):
                        extra += f", {p['pr_count']} PRs"
                    lines.append(f"    {marker} {name} ({status}){extra}")
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
            http_str = f"HTTP {dep.get('http_status')}" if dep.get("http_status") else dep.get("deploy_status", "—")
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
    chains = status.get("active_heal_chains", {})
    if chains:
        lines.append("ACTIVE HEAL CHAINS:")
        for source, c in chains.items():
            results = c.get("step_results", [])
            steps_desc = " → ".join(f"{s['capability']}:{s['result']}" for s in results) or "starting..."
            wait = f"waiting {c.get('cycles_waited', 0)}/{c.get('cycles_to_wait', 0)}" if c.get("awaiting_verification") else "ready"
            lines.append(f"  {source} → {c.get('chain', '?')}")
            lines.append(f"    Step {c.get('step', 0)}, attempts {c.get('total_attempts', 0)}, {wait}")
            lines.append(f"    Progress: {steps_desc}")
    else:
        lines.append("ACTIVE HEAL CHAINS: none")
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


@router.get("/diagnostic-report")
async def diagnostic_report() -> dict[str, Any]:
    """Generate a structured text diagnostic for pasting into Claude."""
    status = await platform_status()
    tenants = await list_tenants()
    actions_resp = await actions(limit=15)
    patterns_resp = await failure_patterns(min_confidence=0.0)
    text = _format_report(
        status=status,
        tenants=tenants,
        actions=actions_resp.get("actions", []),
        patterns=patterns_resp.get("patterns", []),
    )
    return {"report": text, "generated_at": datetime.now(timezone.utc).isoformat()}


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

    context = {
        "status": status_data,
        "tenants": tenants_data.get("tenants", []),
        "heal_chains": status_data.get("active_heal_chains", {}),
        "executions": status_data.get("executions", []),
        "patterns": patterns_data.get("patterns", []),
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

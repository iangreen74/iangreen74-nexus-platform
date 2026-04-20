"""Dogfood Diagnostic Report — main orchestrator.

Generates a Markdown report: system state, tenant readiness,
aggregate analysis, per-run deep dives, and SFN executions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.intelligence import (
    dogfood_aggregate_analyzer as agg,
    dogfood_run_analyzer,
    dogfood_sfn_probe,
)

logger = logging.getLogger("nexus.intelligence.dogfood_diagnostic_report")


def _section_system_state() -> str:
    """Section 1: DogfoodConfig + active batch status."""
    lines = ["## 1. System State", ""]
    config = overwatch_graph.get_dogfood_config()
    if config:
        for k in ("enabled", "activated_by", "activated_at", "tenant_id"):
            lines.append(f"- **{k}:** {config.get(k, 'n/a')}")
        if config.get("paused_at"):
            lines.append(f"- **Paused at:** {config['paused_at']}")
    else:
        lines.append("_No DogfoodConfig found._")
    lines.append("")
    batch = overwatch_graph.get_active_batch()
    if batch:
        lines.append("### Active Batch")
        for k in ("batch_id", "requested", "remaining", "completed", "successes", "failures"):
            lines.append(f"- **{k}:** {batch.get(k, 0)}")
        c, s = int(batch.get("completed") or 0), int(batch.get("successes") or 0)
        lines.append(f"- **Success rate:** {round(s / c * 100, 1) if c else 0.0}%")
    else:
        lines.append("_No active batch._")
    return "\n".join(lines)


def _section_tenant_readiness() -> str:
    """Section 2: Tenant readiness — role, projects, UserContext."""
    lines = ["## 2. Tenant Readiness", ""]
    config = overwatch_graph.get_dogfood_config()
    tid = config.get("tenant_id") if config else None
    if not tid:
        return "\n".join(lines + ["_No tenant configured._"])
    tenant_rows = overwatch_graph.query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "RETURN t.tenant_id AS tenant_id, t.aws_role_arn AS aws_role_arn, "
        "t.name AS name LIMIT 1", {"tid": tid})
    if tenant_rows:
        t = tenant_rows[0]
        lines.append(f"- **Tenant:** {t.get('name', tid)}")
        role = t.get("aws_role_arn")
        lines.append(f"- **AWS Role ARN:** {role or 'NOT SET'}")
    else:
        lines.append(f"_Tenant {tid} not found in graph._")
    project_rows = overwatch_graph.query(
        "MATCH (p:Project {tenant_id: $tid}) "
        "RETURN p.project_id AS project_id, p.name AS name, "
        "p.status AS status LIMIT 20", {"tid": tid})
    lines.append(f"\n### Projects ({len(project_rows)})")
    for p in project_rows[:10]:
        lines.append(f"- `{p.get('project_id', '?')}` — {p.get('name', '?')} ({p.get('status', '?')})")
    if len(project_rows) > 10:
        lines.append(f"- ... and {len(project_rows) - 10} more")
    ctx_rows = overwatch_graph.query(
        "MATCH (u:UserContext {tenant_id: $tid}) "
        "RETURN u.user_id AS user_id, u.provider AS provider LIMIT 5", {"tid": tid})
    lines.append(f"\n### UserContext ({len(ctx_rows)})")
    for u in ctx_rows:
        lines.append(f"- User `{u.get('user_id', '?')}` ({u.get('provider', '?')})")
    if not ctx_rows:
        lines.append("_No UserContext nodes found._")
    return "\n".join(lines)


def _section_aggregate(analyzed_runs: list[dict[str, Any]]) -> str:
    """Section 3: Aggregate analysis across all runs."""
    lines = ["## 3. Aggregate Analysis", ""]
    if not analyzed_runs:
        return "\n".join(lines + ["_No runs to analyze._"])
    lines.append("### Outcome Breakdown")
    for o in agg.cluster_by_outcome(analyzed_runs):
        lines.append(f"- **{o['outcome']}:** {o['count']} ({o['pct']}%)")
    lines.append("\n### Terminal State Clusters")
    for c in agg.cluster_by_terminal_state(analyzed_runs):
        lines.append(f"- **{c['state']}:** {c['count']} runs")
    lines.append("\n### Top Error Messages")
    errors = agg.top_error_messages(analyzed_runs)
    for e in errors:
        lines.append(f"- ({e['count']}x) `{e['message'][:120]}`")
    if not errors:
        lines.append("_No errors captured._")
    lines.append("\n### Tenant Breakdown")
    for t in agg.tenant_breakdown(analyzed_runs):
        lines.append(f"- **{t['tenant_id']}:** {t['total']} runs, {t['success_rate']}% success")
    lines.append("\n### Stage Reachability")
    for s in agg.stage_reachability(analyzed_runs):
        bar = "#" * int(s["pct"] / 5) if s["pct"] > 0 else "-"
        lines.append(f"- {s['stage']:10} {s['reached']:3} ({s['pct']:5.1f}%) {bar}")
    return "\n".join(lines)


def _section_per_run(analyzed_runs: list[dict[str, Any]], limit: int = 10) -> str:
    """Section 4: Per-run deep dive for terminal runs."""
    terminal = [r for r in analyzed_runs if r.get("status") != "pending"]
    terminal.sort(key=lambda r: r.get("completed_at") or "", reverse=True)
    subset = terminal[:limit]
    lines = [f"## 4. Per-Run Deep Dive (top {limit} terminal)", ""]
    if not subset:
        return "\n".join(lines + ["_No terminal runs._"])
    for r in subset:
        rid = (r.get("run_id") or "?")[:12]
        lines.append(f"### Run `{rid}` — {r.get('app_name', '?')}")
        lines.append(f"- Status: **{r.get('status')}** | Tenant: {r.get('tenant_id', 'n/a')}")
        lines.append(f"- Started: {r.get('started_at', '?')} | Completed: {r.get('completed_at', '?')}")
        ts = r.get("terminal_state") or {}
        if ts.get("terminal_state"):
            lines.append(f"- Terminal state: **{ts['terminal_state']}**")
        if ts.get("error"):
            lines.append(f"- Error: `{ts['error'][:150]}`")
        if ts.get("cause"):
            lines.append(f"- Cause: `{ts['cause'][:150]}`")
        lines.append(f"- Pipeline events: {len(r.get('pipeline_events', []))}")
        errs = r.get("error_logs", [])
        if errs:
            lines.append(f"- Error logs ({len(errs)}):")
            for e in errs[:3]:
                lines.append(f"  - `{e[:120]}`")
        lines.append("")
    return "\n".join(lines)


def _section_sfn_executions(hours: int) -> str:
    """Section 5: Recent V2 SFN executions."""
    lines = [f"## 5. V2 SFN Executions (last {hours}h)", ""]
    execs = dogfood_sfn_probe.list_recent_executions(hours=hours, limit=20)
    if not execs:
        return "\n".join(lines + ["_No executions found._"])
    lines += ["| Status | Name | Start | Duration |", "|--------|------|-------|----------|"]
    for ex in execs:
        name = (ex.get("name") or "?")[:30]
        dur = ex.get("duration_ms")
        dur_str = f"{dur / 1000:.1f}s" if dur else "running"
        lines.append(f"| {ex.get('status', '?')} | {name} | {ex.get('start_date', '?')[:19]} | {dur_str} |")
    return "\n".join(lines)


def generate_diagnostic_report(hours: int = 6, run_limit: int = 30) -> str:
    """Generate a comprehensive dogfood diagnostic report in Markdown."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = f"# Dogfood Diagnostic Report\n\n_Generated {ts} — window: {hours}h, limit: {run_limit} runs_\n\n---\n"
    runs = overwatch_graph.list_dogfood_runs(since_hours=hours, limit=run_limit)
    analyzed: list[dict[str, Any]] = []
    for run in runs:
        try:
            analyzed.append(dogfood_run_analyzer.analyze_run(run))
        except Exception:
            logger.exception("Failed to analyze run %s", run.get("id"))
    sections = [
        header, _section_system_state(), _section_tenant_readiness(),
        _section_aggregate(analyzed), _section_per_run(analyzed),
        _section_sfn_executions(hours),
    ]
    return "\n\n---\n\n".join(sections)

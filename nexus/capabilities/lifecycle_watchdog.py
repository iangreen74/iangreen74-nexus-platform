"""
Lifecycle Watchdog — 7 stuck-stage detectors per tenant.

Each check returns a dict with {check, stuck, hours, diagnosis, suggested}
when the tenant is stuck at a specific lifecycle gate, or None if they've
progressed past that gate.

Thresholds reflect typical Forgewing pace: signups convert to ingestion
in minutes, ingestion to brief in ~15min, approval within hours.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Thresholds in hours
THRESHOLDS = {
    "signup_stalled": 1,
    "ingestion_stuck": 1,
    "brief_stuck": 1,
    "approval_stalled": 24,
    "no_prs_after_approval": 2,
    "pr_review_stalled": 48,
    "deploy_not_started": 24,
}

# Mission-stage buckets
_INGEST_STAGES = {"awaiting_repo", "ingestion_pending", "ingesting"}
_BRIEF_STAGES = {"brief_generating", "analyzing"}
_APPROVED_STAGES = {"executing", "complete"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _hours_since(ts: Any) -> float | None:
    dt = _parse_iso(ts)
    return (_now() - dt).total_seconds() / 3600 if dt else None


def check_lifecycle(tenant_id: str, tenant_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return list of active stuck-stage findings for this tenant."""
    if not tenant_id or not isinstance(tenant_data, dict):
        return []

    findings: list[dict[str, Any]] = []
    ctx = tenant_data.get("context") or {}
    pipeline = tenant_data.get("pipeline") or {}
    deployment = tenant_data.get("deployment") or {}
    stage = (ctx.get("mission_stage") or "").strip()

    for fn in (_check_signup, _check_ingestion, _check_brief, _check_approval,
               _check_no_prs, _check_pr_review, _check_deploy_not_started):
        try:
            finding = fn(tenant_id, ctx, pipeline, deployment, stage)
            if finding:
                findings.append(finding)
        except Exception:
            logger.debug("lifecycle check %s failed for %s",
                         fn.__name__, tenant_id, exc_info=True)
    return findings


def _mk(check: str, hours: float | None, diagnosis: str, suggested: str,
        tenant_id: str) -> dict[str, Any]:
    return {
        "check": check,
        "tenant_id": tenant_id,
        "stuck": True,
        "hours": round(hours, 1) if hours else None,
        "threshold_hours": THRESHOLDS[check],
        "diagnosis": diagnosis,
        "suggested": suggested,
    }


def _check_signup(tid, ctx, pipeline, deployment, stage):
    """No stage or created but no activity after signup."""
    if stage and stage != "awaiting_repo":
        return None
    hours = _hours_since(ctx.get("created_at"))
    if hours and hours > THRESHOLDS["signup_stalled"]:
        return _mk("signup_stalled", hours,
                   "Tenant signed up but never connected repo",
                   "Nudge tenant to connect a repo or send onboarding follow-up", tid)
    return None


def _check_ingestion(tid, ctx, pipeline, deployment, stage):
    """Stuck in ingestion stages."""
    if stage not in _INGEST_STAGES:
        return None
    hours = _hours_since(ctx.get("updated_at") or ctx.get("created_at"))
    if hours and hours > THRESHOLDS["ingestion_stuck"]:
        return _mk("ingestion_stuck", hours,
                   f"Ingestion has been in '{stage}' for {hours:.1f}h",
                   "Check ingestion logs, retrigger_ingestion if needed", tid)
    return None


def _check_brief(tid, ctx, pipeline, deployment, stage):
    """Brief generation hanging."""
    if stage not in _BRIEF_STAGES:
        return None
    hours = _hours_since(ctx.get("updated_at"))
    if hours and hours > THRESHOLDS["brief_stuck"]:
        return _mk("brief_stuck", hours,
                   f"Brief generation has been in '{stage}' for {hours:.1f}h",
                   "Check Bedrock logs, retrigger brief generation", tid)
    return None


def _check_approval(tid, ctx, pipeline, deployment, stage):
    """Brief generated but user never approved."""
    if stage != "awaiting_approval":
        return None
    hours = _hours_since(ctx.get("updated_at"))
    if hours and hours > THRESHOLDS["approval_stalled"]:
        return _mk("approval_stalled", hours,
                   f"Brief awaiting user approval for {hours:.1f}h",
                   "Send reminder email/notification to tenant", tid)
    return None


def _check_no_prs(tid, ctx, pipeline, deployment, stage):
    """Approved but no PRs emerging after reasonable time."""
    if stage not in _APPROVED_STAGES:
        return None
    pr_count = pipeline.get("pr_count", 0)
    if pr_count > 0:
        return None
    hours = _hours_since(ctx.get("updated_at"))
    if hours and hours > THRESHOLDS["no_prs_after_approval"]:
        return _mk("no_prs_after_approval", hours,
                   f"Approved {hours:.1f}h ago but 0 PRs produced",
                   "Check daemon cycles, validate tenant onboarding", tid)
    return None


def _check_pr_review(tid, ctx, pipeline, deployment, stage):
    """PRs open for too long — customer not merging."""
    last_pr = pipeline.get("last_pr_at")
    pr_count = pipeline.get("pr_count", 0)
    if pr_count == 0:
        return None
    hours = _hours_since(last_pr)
    if hours and hours > THRESHOLDS["pr_review_stalled"]:
        return _mk("pr_review_stalled", hours,
                   f"Latest PR is {hours:.1f}h old — not being merged",
                   "Nudge tenant to review PRs", tid)
    return None


def _check_deploy_not_started(tid, ctx, pipeline, deployment, stage):
    """Merged PRs but no deployment attempt."""
    if deployment.get("provisioned"):
        return None
    if stage not in _APPROVED_STAGES:
        return None
    hours = _hours_since(ctx.get("updated_at"))
    if hours and hours > THRESHOLDS["deploy_not_started"]:
        return _mk("deploy_not_started", hours,
                   f"Tenant active for {hours:.1f}h with no deploy attempt",
                   "Check deploy readiness, prompt tenant to deploy", tid)
    return None


def format_for_report(findings_by_tenant: dict[str, list[dict[str, Any]]]) -> str:
    """Format all tenants' lifecycle findings for the diagnostic report."""
    if not findings_by_tenant:
        return "LIFECYCLE WATCHDOG: all tenants progressing normally"
    lines = ["LIFECYCLE WATCHDOG:"]
    for tid, findings in findings_by_tenant.items():
        if not findings:
            lines.append(f"  {tid}: ok")
            continue
        for f in findings:
            hrs = f"{f.get('hours', 0):.1f}h" if f.get("hours") else "?"
            lines.append(f"  {tid}: {f['check']} ({hrs}) — {f['diagnosis']}")
    return "\n".join(lines)

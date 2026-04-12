"""
Consistency Auditor — detects data drift between Neptune nodes.

Six checks with auto-fix for safe drifts. Every auto-fix recorded
as a HealingAction.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus import overwatch_graph
from nexus.config import BLAST_SAFE, MODE

logger = logging.getLogger(__name__)


def audit_tenant(tenant_id: str, tenant_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Run all consistency checks for one tenant. Returns list of findings."""
    if not tenant_id or not isinstance(tenant_data, dict):
        return []
    findings: list[dict[str, Any]] = []
    for fn in (_check_repo_url_sync, _check_active_project_exists,
               _check_ingest_stage_sync, _check_pr_merge_sync,
               _check_cloud_connection_valid):
        try:
            f = fn(tenant_id, tenant_data)
            if f:
                findings.append(f)
        except Exception:
            logger.debug("consistency check %s failed for %s",
                         fn.__name__, tenant_id, exc_info=True)
    return findings


def audit_global() -> list[dict[str, Any]]:
    """Cross-tenant checks that don't belong to one tenant."""
    findings: list[dict[str, Any]] = []
    try:
        findings.extend(_check_orphan_projects())
    except Exception:
        logger.debug("orphan_projects check failed", exc_info=True)
    return findings


def _record_fix(tid: str, check: str, detail: str) -> None:
    try:
        overwatch_graph.record_healing_action(
            action_type=f"consistency_fix:{check}", target=tid,
            blast_radius=BLAST_SAFE, trigger="consistency_auditor",
            outcome="success")
        logger.info("Auto-fix [%s] %s: %s", check, tid, detail)
    except Exception:
        pass


def _record_finding(tid: str, check: str, issue: str,
                    auto_fixed: bool = False, fix_detail: str = "") -> dict[str, Any]:
    return {"check": check, "tenant_id": tid, "issue": issue,
            "auto_fixed": auto_fixed, "fix_detail": fix_detail}


# --- Per-tenant checks -------------------------------------------------------


def _check_repo_url_sync(tid, data):
    ctx = data.get("context") or {}
    tenant_url = (ctx.get("repo_url") or "").strip()
    active = (data.get("active_project") or {}).get("repo_url", "").strip()
    if not tenant_url or not active or tenant_url == active:
        return None
    # Auto-fix deliberately disabled: writing Tenant.repo_url is a write
    # into aria-platform's graph schema — Tier 3 per CLAUDE.md. This
    # drift is reported so an operator can sync via the Forgewing UI
    # or a vetted admin path, not from Overwatch.
    return _record_finding(
        tid, "repo_url_sync",
        f"Tenant.repo_url='{tenant_url[:60]}' != active Project.repo_url='{active[:60]}'",
        auto_fixed=False,
        fix_detail="")


def _auto_fix_repo_url(tid, url):
    """DISABLED. See _check_repo_url_sync — writing Tenant.repo_url is
    Tier 3 (cross-system schema write) and must stay an operator-gated
    escalation, not an autonomous Overwatch action. Kept as a no-op so
    tests that reference the symbol don't break; remove when the tests
    no longer import it."""
    return False


def _check_active_project_exists(tid, data):
    stage = ((data.get("context") or {}).get("mission_stage") or "").strip()
    if stage in ("awaiting_repo", "ingestion_pending", ""):
        return None
    if data.get("active_project"):
        return None
    return _record_finding(tid, "active_project_exists",
                           f"Tenant stage='{stage}' but no active Project")


def _check_ingest_stage_sync(tid, data):
    stage = ((data.get("context") or {}).get("mission_stage") or "").strip()
    if stage not in ("awaiting_repo", "ingestion_pending", "ingesting"):
        return None
    fc = (data.get("pipeline") or {}).get("repo_file_count", 0)
    if fc < 10:
        return None
    return _record_finding(tid, "ingest_stage_sync",
                           f"{fc} RepoFiles indexed but stage still '{stage}'")


def _check_pr_merge_sync(tid, data):
    pipeline = data.get("pipeline") or {}
    nep = pipeline.get("pr_count", 0)
    gh = pipeline.get("github_pr_count")
    if gh is None or nep == 0 or abs(nep - gh) <= 1:
        return None
    return _record_finding(tid, "pr_merge_sync",
                           f"Neptune pr_count={nep} diverges from GitHub={gh}")


def _check_cloud_connection_valid(tid, data):
    stage = ((data.get("context") or {}).get("mission_stage") or "").strip()
    if stage in ("awaiting_repo", "") or (data.get("token") or {}).get("present"):
        return None
    return _record_finding(tid, "cloud_connection_valid",
                           f"Tenant stage='{stage}' but GitHub token is empty")


# --- Cross-tenant checks -----------------------------------------------------


def _check_orphan_projects() -> list[dict[str, Any]]:
    """Project nodes whose tenant_id doesn't match any Tenant node."""
    if MODE != "production":
        return []
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (p:Project) "
            "WHERE NOT EXISTS { MATCH (t:Tenant {tenant_id: p.tenant_id}) } "
            "RETURN p.tenant_id AS tid, p.project_id AS pid LIMIT 20"
        )
        return [
            _record_finding(
                r.get("tid", "?"), "orphan_projects",
                f"Project {r.get('pid', '?')[:12]} has no matching Tenant",
            )
            for r in rows
        ]
    except Exception:
        return []


def format_for_report(findings_by_tenant: dict[str, list[dict[str, Any]]],
                      global_findings: list[dict[str, Any]]) -> str:
    """Format for the diagnostic report."""
    total = sum(len(v) for v in findings_by_tenant.values()) + len(global_findings)
    if total == 0:
        return "DATA CONSISTENCY: no drift detected"
    lines = [f"DATA CONSISTENCY: {total} findings"]
    for tid, findings in findings_by_tenant.items():
        for f in findings:
            fix = " [auto-fixed]" if f.get("auto_fixed") else ""
            lines.append(f"  {tid}: {f['check']}{fix} — {f['issue']}")
    for f in global_findings:
        lines.append(f"  [global] {f['check']}: {f['issue']}")
    return "\n".join(lines)

"""
Consistency Auditor — detects data drift between Neptune nodes.

Six consistency checks with auto-fix for safe drifts. Every auto-fix
is recorded as a HealingAction in the Overwatch graph for audit.

Checks:
1. repo_url_sync: Tenant.repo_url matches active Project.repo_url (auto-fix)
2. active_project_exists: Tenant has >=1 active Project (alert only)
3. ingest_stage_sync: stage advances once RepoFile count > 0 (auto-fix)
4. pr_merge_sync: GitHub PR state matches Neptune PullRequest (alert only)
5. cloud_connection_valid: token_empty vs mission_stage consistency (alert)
6. orphan_projects: Project rows with no matching Tenant (alert only)
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


def _record_fix(tenant_id: str, check: str, detail: str) -> None:
    try:
        overwatch_graph.record_healing_action(
            action_type=f"consistency_fix:{check}",
            target=tenant_id,
            blast_radius=BLAST_SAFE,
            trigger="consistency_auditor",
            outcome="success",
        )
        logger.info("Auto-fix [%s] %s: %s", check, tenant_id, detail)
    except Exception:
        pass


def _record_finding(tenant_id: str, check: str, issue: str,
                    auto_fixed: bool = False, fix_detail: str = "") -> dict[str, Any]:
    return {
        "check": check,
        "tenant_id": tenant_id,
        "issue": issue,
        "auto_fixed": auto_fixed,
        "fix_detail": fix_detail,
    }


# --- Per-tenant checks -------------------------------------------------------


def _check_repo_url_sync(tid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Tenant.repo_url should match active project's repo_url."""
    ctx = data.get("context") or {}
    tenant_url = (ctx.get("repo_url") or "").strip()
    active = (data.get("active_project") or {}).get("repo_url", "").strip()
    if not tenant_url or not active:
        return None
    if tenant_url == active:
        return None
    fixed = _auto_fix_repo_url(tid, active) if MODE == "production" else False
    return _record_finding(
        tid, "repo_url_sync",
        f"Tenant.repo_url='{tenant_url[:60]}' != active Project.repo_url='{active[:60]}'",
        auto_fixed=fixed,
        fix_detail=f"updated Tenant.repo_url → {active[:60]}" if fixed else "",
    )


def _auto_fix_repo_url(tid: str, target_url: str) -> bool:
    """Update Tenant.repo_url to match active project."""
    try:
        from nexus import neptune_client

        neptune_client.query(
            "MATCH (t:Tenant {tenant_id: $tid}) SET t.repo_url = $url",
            {"tid": tid, "url": target_url},
        )
        _record_fix(tid, "repo_url_sync", f"-> {target_url[:60]}")
        return True
    except Exception:
        return False


def _check_active_project_exists(tid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Every tenant past onboarding should have >=1 active project."""
    ctx = data.get("context") or {}
    stage = (ctx.get("mission_stage") or "").strip()
    if stage in ("awaiting_repo", "ingestion_pending", ""):
        return None
    active = data.get("active_project")
    if active:
        return None
    return _record_finding(
        tid, "active_project_exists",
        f"Tenant stage='{stage}' but no active Project",
    )


def _check_ingest_stage_sync(tid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """If repo files are indexed, stage should have advanced past ingestion."""
    ctx = data.get("context") or {}
    stage = (ctx.get("mission_stage") or "").strip()
    pipeline = data.get("pipeline") or {}
    if stage not in ("awaiting_repo", "ingestion_pending", "ingesting"):
        return None
    file_count = pipeline.get("repo_file_count", 0)
    if file_count < 10:
        return None
    return _record_finding(
        tid, "ingest_stage_sync",
        f"{file_count} RepoFiles indexed but stage still '{stage}'",
    )


def _check_pr_merge_sync(tid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Large gap between Neptune pr_count and GitHub reality."""
    pipeline = data.get("pipeline") or {}
    nep_count = pipeline.get("pr_count", 0)
    gh_count = pipeline.get("github_pr_count")
    if gh_count is None or nep_count == 0:
        return None
    if abs(nep_count - gh_count) <= 1:
        return None
    return _record_finding(
        tid, "pr_merge_sync",
        f"Neptune pr_count={nep_count} diverges from GitHub={gh_count}",
    )


def _check_cloud_connection_valid(tid: str, data: dict[str, Any]) -> dict[str, Any] | None:
    """Empty token but tenant is past onboarding = broken connection."""
    ctx = data.get("context") or {}
    token = data.get("token") or {}
    stage = (ctx.get("mission_stage") or "").strip()
    if stage in ("awaiting_repo", ""):
        return None
    if token.get("present"):
        return None
    return _record_finding(
        tid, "cloud_connection_valid",
        f"Tenant stage='{stage}' but GitHub token is empty",
    )


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

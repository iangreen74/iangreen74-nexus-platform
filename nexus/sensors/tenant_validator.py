"""
Tenant Validator — proactive health validation for every tenant.

Instead of waiting for customer-visible failures, actively validates
every active tenant on each Overwatch poll cycle:

1. Token is valid and non-empty
2. RepoFile nodes exist in Neptune (repo was indexed)
3. Tasks are progressing (not stuck)
4. Tenant has a mission_stage consistent with their graph state

Any failure produces a ValidationAlert with a diagnosis and
suggested_action. The alert can feed directly into triage for
auto-healing or escalation.

This is the sensor that prevents "the Ben experience" from recurring —
every onboarding gap that blocked Ben is now caught proactively.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import aws_client, neptune_client
from nexus.config import MODE

logger = logging.getLogger("nexus.sensors.tenant_validator")

# Stages where ingestion should be complete (RepoFile count > 0)
_POST_INGESTION_STAGES = frozenset({
    "brief_pending",
    "brief_pending_approval",
    "executing",
    "complete",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(
    tenant_id: str,
    check: str,
    severity: str,
    message: str,
    action: str = "",
) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "check": check,
        "severity": severity,
        "message": message,
        "suggested_action": action,
        "checked_at": _now().isoformat(),
    }


def validate_token(tenant_id: str) -> list[dict[str, Any]]:
    """Check that the tenant has a non-empty GitHub token."""
    alerts: list[dict[str, Any]] = []
    secret_name = f"forgescaler/tenant/{tenant_id}/github-token"
    secret = aws_client.get_secret(secret_name)
    token = secret.get("github_token") or secret.get("_raw", "")
    installation_id = secret.get("installation_id")

    if not installation_id:
        alerts.append(_alert(
            tenant_id, "installation_id_missing", "critical",
            "No installation_id in tenant secret — GitHub App not connected.",
            "Guide customer to install the GitHub App.",
        ))
    if not token:
        alerts.append(_alert(
            tenant_id, "token_empty", "warning",
            "github_token is empty. Daemon can't access this tenant's repo.",
            "refresh_tenant_token should auto-mint a fresh token.",
        ))
    return alerts


def validate_indexing(tenant_id: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    """Check that RepoFile nodes exist when they should."""
    stage = context.get("mission_stage", "")
    if stage not in _POST_INGESTION_STAGES:
        return []
    files = neptune_client.query(
        "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
        {"tid": tenant_id},
    )
    count = int(files[0].get("c", 0)) if files else 0
    if count == 0:
        return [_alert(
            tenant_id, "missing_repo_files", "critical",
            f"Tenant at stage '{stage}' but has 0 RepoFile nodes — ingestion failed or never ran.",
            "retrigger_ingestion via Forgewing API.",
        )]
    return []


def validate_task_progress(tenant_id: str) -> list[dict[str, Any]]:
    """Flag tasks that are stuck in non-terminal states."""
    alerts: list[dict[str, Any]] = []
    tasks = neptune_client.get_recent_tasks(tenant_id, limit=50)
    now = _now()
    for task in tasks:
        status = task.get("status")
        created_raw = task.get("created_at")
        if not created_raw or status not in ("pending", "in_progress", "in_review"):
            continue
        try:
            created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        age_h = (now - created).total_seconds() / 3600.0
        if status == "pending" and age_h > 4:
            alerts.append(_alert(
                tenant_id, "task_stuck_pending", "warning",
                f"Task {task.get('id')} stuck in 'pending' for {age_h:.0f}h",
                "Investigate daemon pipeline for this tenant.",
            ))
        elif status == "in_progress" and age_h > 6:
            alerts.append(_alert(
                tenant_id, "task_stuck_in_progress", "warning",
                f"Task {task.get('id')} stuck in 'in_progress' for {age_h:.0f}h",
                "May be blocked on Bedrock, GitHub write, or a bug in task_executor.",
            ))
    return alerts


def validate_tenant(tenant_id: str) -> list[dict[str, Any]]:
    """
    Run all validation checks for a single tenant. Returns a combined
    alert list (may be empty = healthy).
    """
    all_alerts: list[dict[str, Any]] = []
    ctx = neptune_client.get_tenant_context(tenant_id)
    for check_fn in (
        lambda: validate_token(tenant_id),
        lambda: validate_indexing(tenant_id, ctx),
        lambda: validate_task_progress(tenant_id),
    ):
        try:
            all_alerts.extend(check_fn())
        except Exception:
            logger.exception("validate_tenant(%s) check failed", tenant_id)
    return all_alerts


def validate_all_tenants() -> dict[str, list[dict[str, Any]]]:
    """
    Run all validation checks for every active tenant.

    Returns {tenant_id: [alerts]}. Tenants with no alerts are healthy
    and are included as empty lists.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    for tid in neptune_client.get_tenant_ids():
        results[tid] = validate_tenant(tid)
    return results

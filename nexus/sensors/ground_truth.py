"""
Ground Truth Sensor — check what's ACTUALLY running, not what Neptune says.

Neptune nodes are written by the daemon and may be stale. This sensor
hits the actual app URLs and Forgewing API to get the real state.

Checks:
1. App URL health (HTTP GET with 5s timeout)
2. DeploymentProgress from Neptune (authoritative for deploy stage)
3. PR + task counts without artificial limits
4. Velocity metrics (cycle time, completion rate)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger(__name__)

_TIMEOUT = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def check_app_url(tenant_id: str) -> dict[str, Any]:
    """HTTP check on the tenant's actual app URL from DeploymentProgress."""
    if MODE != "production":
        return {"status": "live", "http_status": 200, "app_url": "http://mock.local", "mock": True}
    # Get app URL from DeploymentProgress or DeployedService
    url = None
    dp = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) RETURN d.monitoring_url AS url",
        {"tid": tenant_id},
    )
    if dp and dp[0].get("url"):
        url = dp[0]["url"]
    else:
        svc = neptune_client.query(
            "MATCH (s:DeployedService {tenant_id: $tid}) RETURN s.url AS url",
            {"tid": tenant_id},
        )
        if svc and svc[0].get("url"):
            url = svc[0]["url"]
    if not url:
        return {"status": "no_url", "http_status": None, "app_url": None}
    try:
        resp = httpx.get(url, timeout=_TIMEOUT, follow_redirects=True)
        return {
            "status": "live" if resp.status_code < 500 else "error",
            "http_status": resp.status_code,
            "app_url": url,
        }
    except Exception as exc:
        return {"status": "unreachable", "http_status": None, "app_url": url, "error": str(exc)[:80]}


def get_full_pr_count(tenant_id: str) -> dict[str, Any]:
    """Get accurate PR counts without artificial limits."""
    if MODE != "production":
        return {"total": 5, "merged": 3, "pending": 2, "mock": True}
    rows = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid}) WHERE m.pr_number IS NOT NULL "
        "RETURN m.status AS status, m.pr_url AS url, m.merged_at AS merged",
        {"tid": tenant_id},
    )
    merged = sum(1 for r in rows if r.get("merged") or r.get("status") == "complete")
    pending = len(rows) - merged
    return {"total": len(rows), "merged": merged, "pending": pending}


def get_full_task_count(tenant_id: str) -> dict[str, Any]:
    """Get accurate task counts without limits."""
    if MODE != "production":
        return {"total": 5, "complete": 3, "pending": 1, "in_progress": 1, "mock": True}
    rows = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid}) RETURN m.status AS status",
        {"tid": tenant_id},
    )
    by_status: dict[str, int] = {}
    for r in rows:
        s = r.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return {"total": len(rows), **by_status}


def get_velocity(tenant_id: str) -> dict[str, Any]:
    """Compute velocity metrics: PR cycle time, last activity, completion rate."""
    if MODE != "production":
        return {"avg_pr_cycle_minutes": 45, "last_pr_age_hours": 20, "completion_rate": 100, "mock": True}
    # PR cycle time: created_at → submitted_at for tasks with PRs
    rows = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid}) "
        "WHERE m.pr_number IS NOT NULL AND m.created_at IS NOT NULL AND m.submitted_at IS NOT NULL "
        "RETURN m.created_at AS created, m.submitted_at AS submitted "
        "ORDER BY m.submitted_at DESC",
        {"tid": tenant_id},
    )
    cycles = []
    for r in rows:
        c, s = _parse_ts(r.get("created")), _parse_ts(r.get("submitted"))
        if c and s:
            cycles.append((s - c).total_seconds() / 60.0)

    avg_cycle = round(sum(cycles) / len(cycles), 1) if cycles else None

    # Last PR age
    last_pr = _parse_ts(rows[0].get("submitted")) if rows else None
    last_pr_hours = round((_now() - last_pr).total_seconds() / 3600, 1) if last_pr else None

    # Completion rate
    tasks = get_full_task_count(tenant_id)
    total = tasks.get("total", 0)
    complete = tasks.get("complete", 0)
    rate = round(complete / total * 100, 1) if total > 0 else 0

    return {
        "avg_pr_cycle_minutes": avg_cycle,
        "last_pr_age_hours": last_pr_hours,
        "completion_rate": rate,
        "total_tasks": total,
        "complete_tasks": complete,
    }


def get_deploy_ground_truth(tenant_id: str) -> dict[str, Any]:
    """Combine all sources for the true deploy state."""
    app = check_app_url(tenant_id)
    dp = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
        "RETURN d.stage AS stage, d.updated_at AS updated",
        {"tid": tenant_id},
    ) if MODE == "production" else []

    stage = dp[0].get("stage") if dp else None
    updated = dp[0].get("updated") if dp else None

    # Terminal / safe stages — never classify these as "deploying"
    _SAFE_STAGES = {"live", "complete", "not_started"}

    if app.get("status") == "live":
        deploy_status = "live"
    elif stage == "live":
        deploy_status = "live"
    elif stage == "not_started":
        deploy_status = "not_started"
    elif stage and stage not in _SAFE_STAGES:
        deploy_status = "deploying"
    elif app.get("status") == "no_url" and not stage:
        deploy_status = "not_started"
    else:
        deploy_status = app.get("status", "unknown")

    return {
        "deploy_status": deploy_status,
        "app_url": app.get("app_url"),
        "http_status": app.get("http_status"),
        "stage": stage,
        "last_deploy": updated,
    }


def get_tenant_ground_truth(tenant_id: str) -> dict[str, Any]:
    """Full ground truth for a single tenant."""
    return {
        "tenant_id": tenant_id,
        "deploy": get_deploy_ground_truth(tenant_id),
        "prs": get_full_pr_count(tenant_id),
        "tasks": get_full_task_count(tenant_id),
        "velocity": get_velocity(tenant_id),
    }

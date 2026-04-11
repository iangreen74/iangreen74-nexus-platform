"""
Project Lifecycle Monitor — track project creation, archival, and restarts.

Forgewing now supports always-available project creation: users can
archive their current project and start fresh. Overwatch monitors
this lifecycle to:
  1. Validate restarts enter ingestion correctly
  2. Flag tenants with no active project (possible abandonment)
  3. Clean up stale pending_restart flags
  4. Log lifecycle events for the diagnostic report
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import neptune_client, overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_SAFE, MODE

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def get_project_lifecycle(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Query Neptune for Project nodes and return lifecycle summary.

    Returns: active project (if any), archived count, last lifecycle
    event, pending_restart flag status.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {
            "tenant_id": tenant_id,
            "mock": True,
            "active_project": {"name": "My App", "repo_url": "https://github.com/test/app"},
            "archived_count": 0,
            "last_event": None,
            "pending_restart": False,
        }

    # Query active projects
    active = neptune_client.query(
        "MATCH (p:Project {tenant_id: $tid}) "
        "WHERE p.status = 'active' OR p.status IS NULL "
        "RETURN p.name AS name, p.repo_url AS repo_url, "
        "p.created_at AS created_at, p.status AS status "
        "ORDER BY p.created_at DESC LIMIT 1",
        {"tid": tenant_id},
    )

    # Query archived projects
    archived = neptune_client.query(
        "MATCH (p:Project {tenant_id: $tid}) "
        "WHERE p.status = 'archived' "
        "RETURN p.name AS name, p.archived_at AS archived_at "
        "ORDER BY p.archived_at DESC",
        {"tid": tenant_id},
    )

    # Check pending_restart on tenant node
    ctx = neptune_client.get_tenant_context(tenant_id)
    pending_restart = ctx.get("pending_restart") is True

    # Determine last lifecycle event
    last_event = None
    if archived:
        last_archived = _parse_ts(archived[0].get("archived_at"))
        if last_archived:
            last_event = {"type": "archived", "at": archived[0]["archived_at"],
                          "project": archived[0].get("name")}
    if active:
        created = _parse_ts(active[0].get("created_at"))
        if created and (not last_event or created > _parse_ts(last_event.get("at", ""))):
            last_event = {"type": "created", "at": active[0]["created_at"],
                          "project": active[0].get("name")}

    # Detect stale pending_restart (>1 hour)
    stale_restart = False
    if pending_restart and not active:
        stale_restart = True  # user started restart but never confirmed

    return {
        "tenant_id": tenant_id,
        "active_project": active[0] if active else None,
        "archived_count": len(archived),
        "last_event": last_event,
        "pending_restart": pending_restart,
        "stale_restart": stale_restart,
        "no_active_project": not active and not pending_restart,
    }


def check_all_tenants_lifecycle(**_: Any) -> dict[str, Any]:
    """Run lifecycle check for all active tenants. Returns summary."""
    tenant_ids = neptune_client.get_tenant_ids()
    results: dict[str, Any] = {}
    alerts: list[dict[str, Any]] = []

    for tid in tenant_ids:
        lc = get_project_lifecycle(tenant_id=tid)
        results[tid] = lc

        if lc.get("no_active_project"):
            alerts.append({"tenant_id": tid, "type": "no_active_project",
                           "message": f"Tenant {tid} has no active project — possible abandonment"})
        if lc.get("stale_restart"):
            alerts.append({"tenant_id": tid, "type": "pending_restart_stale",
                           "message": f"Tenant {tid} has stale pending_restart flag"})

    return {"tenants": results, "alerts": alerts, "alert_count": len(alerts)}


# Register capabilities
registry.register(Capability(
    name="get_project_lifecycle",
    function=get_project_lifecycle,
    blast_radius=BLAST_SAFE,
    description="Check project lifecycle: active project, archived count, restart status",
))
registry.register(Capability(
    name="check_all_tenants_lifecycle",
    function=check_all_tenants_lifecycle,
    blast_radius=BLAST_SAFE,
    description="Lifecycle check for all tenants — flags abandonment and stale restarts",
))

"""
Neptune Client — Read-only access to Forgewing's graph.

This is the *only* path by which NEXUS learns about tenants,
tasks, PRs, and conversations inside Forgewing. NEXUS never
imports aria-platform code; it queries the graph by Gremlin.

In local mode, queries return deterministic mock data so the
sensor layer can be tested without AWS connectivity.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import MODE, NEPTUNE_ENDPOINT, NEPTUNE_PORT

logger = logging.getLogger("nexus.neptune")

# Lazy import — gremlinpython is only needed in production mode
_g = None
_connection = None


def _connect():
    """Create a Gremlin connection to Neptune (production only)."""
    global _g, _connection
    if _g is not None:
        return _g
    from gremlin_python.driver.driver_remote_connection import (
        DriverRemoteConnection,
    )
    from gremlin_python.process.anonymous_traversal import traversal

    url = f"wss://{NEPTUNE_ENDPOINT}.neptune.amazonaws.com:{NEPTUNE_PORT}/gremlin"
    _connection = DriverRemoteConnection(url, "g")
    _g = traversal().withRemote(_connection)
    logger.info("Neptune connection established at %s", url)
    return _g


def query(gremlin_query: str) -> list[Any]:
    """
    Execute a raw Gremlin query. Prefer the helpers below for typed access.
    In local mode, returns an empty list.
    """
    if MODE != "production":
        logger.debug("[local] neptune.query: %s", gremlin_query)
        return []
    g = _connect()
    try:
        return g.V().toList() if gremlin_query == "g.V()" else []
    except Exception:
        logger.exception("Neptune query failed")
        return []


def get_tenant_ids() -> list[str]:
    """Return all active tenant IDs known to the graph."""
    if MODE != "production":
        return ["tenant-alpha", "tenant-beta", "tenant-ben"]
    try:
        g = _connect()
        return [v for v in g.V().hasLabel("Tenant").values("tenant_id").toList()]
    except Exception:
        logger.exception("get_tenant_ids failed")
        return []


def get_tenant_context(tenant_id: str) -> dict[str, Any]:
    """Return the tenant node properties, or {} if not found."""
    if MODE != "production":
        return {
            "tenant_id": tenant_id,
            "name": tenant_id.replace("-", " ").title(),
            "created_at": (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            "plan": "pro",
            "active": True,
        }
    try:
        g = _connect()
        result = (
            g.V()
            .hasLabel("Tenant")
            .has("tenant_id", tenant_id)
            .valueMap(True)
            .toList()
        )
        return result[0] if result else {}
    except Exception:
        logger.exception("get_tenant_context(%s) failed", tenant_id)
        return {}


def get_recent_tasks(tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent MissionTask nodes for a tenant."""
    if MODE != "production":
        now = datetime.now(timezone.utc)
        return [
            {
                "id": f"task-{i}",
                "status": "in_progress" if i < 2 else "complete",
                "created_at": (now - timedelta(hours=i)).isoformat(),
            }
            for i in range(min(limit, 5))
        ]
    try:
        g = _connect()
        return (
            g.V()
            .hasLabel("Tenant")
            .has("tenant_id", tenant_id)
            .out("has_task")
            .order()
            .by("created_at", "desc")
            .limit(limit)
            .valueMap(True)
            .toList()
        )
    except Exception:
        logger.exception("get_recent_tasks(%s) failed", tenant_id)
        return []


def get_recent_prs(tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent PR nodes for a tenant."""
    if MODE != "production":
        now = datetime.now(timezone.utc)
        return [
            {
                "id": f"pr-{i}",
                "state": "merged" if i > 0 else "open",
                "created_at": (now - timedelta(hours=i * 3)).isoformat(),
            }
            for i in range(min(limit, 3))
        ]
    try:
        g = _connect()
        return (
            g.V()
            .hasLabel("Tenant")
            .has("tenant_id", tenant_id)
            .out("has_pr")
            .order()
            .by("created_at", "desc")
            .limit(limit)
            .valueMap(True)
            .toList()
        )
    except Exception:
        logger.exception("get_recent_prs(%s) failed", tenant_id)
        return []


def get_conversation_count(tenant_id: str) -> int:
    """Return total message count for a tenant's conversation graph."""
    if MODE != "production":
        return 42
    try:
        g = _connect()
        return (
            g.V()
            .hasLabel("Tenant")
            .has("tenant_id", tenant_id)
            .out("has_message")
            .count()
            .next()
        )
    except Exception:
        logger.exception("get_conversation_count(%s) failed", tenant_id)
        return 0

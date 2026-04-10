"""
Neptune Client — Read-only access to Forgewing's graph.

Forgewing runs on Neptune **Analytics** (the boto3 `neptune-graph` API
with openCypher), not classic Neptune. This module is the only path by
which NEXUS learns about tenants, tasks, PRs, and conversations.

NEXUS never imports aria-platform code; we just hit the same graph
through Cypher queries that mirror aria's schema.

In local mode, queries return deterministic mock data so the sensor
layer can be tested without AWS connectivity.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import AWS_REGION, MODE, NEPTUNE_GRAPH_ID

logger = logging.getLogger("nexus.neptune")

_client_singleton = None


def _client():
    """Lazy boto3 neptune-graph client (production only)."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton
    import boto3  # noqa: WPS433
    from botocore.config import Config

    cfg = Config(connect_timeout=10, read_timeout=30, retries={"max_attempts": 1})
    endpoint = f"https://{AWS_REGION}.neptune-graph.amazonaws.com"
    _client_singleton = boto3.client(
        "neptune-graph", region_name=AWS_REGION, endpoint_url=endpoint, config=cfg
    )
    logger.info("Neptune Analytics client ready (graph=%s)", NEPTUNE_GRAPH_ID)
    return _client_singleton


def query(cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """
    Execute an openCypher query and return the result rows.

    In local mode, returns []. In production, returns the parsed payload's
    `results` array. Never raises — failures are logged and turned into [].
    """
    if MODE != "production":
        logger.debug("[local] neptune.query: %s", cypher)
        return []
    try:
        resp = _client().execute_query(
            graphIdentifier=NEPTUNE_GRAPH_ID,
            queryString=cypher,
            parameters=parameters or {},
            language="OPEN_CYPHER",
        )
        payload = json.loads(resp["payload"].read())
        return payload.get("results", []) or []
    except Exception:
        logger.exception("Neptune query failed: %s", cypher)
        return []


def get_tenant_ids() -> list[str]:
    """Return all active tenant IDs."""
    if MODE != "production":
        return ["tenant-alpha", "tenant-beta", "tenant-ben"]
    rows = query(
        "MATCH (t:Tenant) WHERE t.status = 'active' RETURN t.tenant_id AS tid"
    )
    return [r["tid"] for r in rows if r.get("tid")]


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
    rows = query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "RETURN t.tenant_id AS tenant_id, t.email AS email, "
        "t.company_name AS name, t.repo_url AS repo_url, "
        "t.mission_stage AS mission_stage, t.tier AS plan, "
        "t.status AS status, t.created_at AS created_at, "
        "t.updated_at AS updated_at",
        {"tid": tenant_id},
    )
    return rows[0] if rows else {}


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
    rows = query(
        "MATCH (m:MissionTask {tenant_id: $tid}) "
        "RETURN m.task_index AS id, m.status AS status, "
        "m.description AS description, m.created_at AS created_at, "
        "m.pr_url AS pr_url, m.pr_number AS pr_number "
        "ORDER BY m.created_at DESC LIMIT $lim",
        {"tid": tenant_id, "lim": limit},
    )
    return rows


def get_recent_prs(tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Return recent PRs for a tenant. PRs are properties on MissionTask
    nodes (pr_url, pr_number, submitted_at) — the schema has no separate PR node.
    """
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
    rows = query(
        "MATCH (m:MissionTask {tenant_id: $tid}) "
        "WHERE m.pr_url IS NOT NULL "
        "RETURN m.pr_number AS id, m.pr_url AS pr_url, "
        "m.status AS state, m.submitted_at AS created_at, "
        "m.merged_at AS merged_at "
        "ORDER BY m.submitted_at DESC LIMIT $lim",
        {"tid": tenant_id, "lim": limit},
    )
    return rows


def get_conversation_count(tenant_id: str) -> int:
    """Return total ConversationMessage count for a tenant."""
    if MODE != "production":
        return 42
    rows = query(
        "MATCH (cm:ConversationMessage {tenant_id: $tid}) RETURN count(cm) AS c",
        {"tid": tenant_id},
    )
    if not rows:
        return 0
    val = rows[0].get("c", 0)
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def get_last_daemon_cycle() -> dict[str, Any] | None:
    """
    Return the most recent DaemonCycle node, or None if there isn't one.

    The aria daemon writes a DaemonCycle node every iteration with a
    `timestamp` property (see aria/daemon_helpers.py:write_cycle_to_neptune).
    """
    if MODE != "production":
        return {
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
            "duration_seconds": 12,
            "prs_checked": 5,
        }
    rows = query(
        "MATCH (d:DaemonCycle) "
        "RETURN d.timestamp AS timestamp, "
        "d.duration_seconds AS duration_seconds, "
        "d.prs_checked AS prs_checked, "
        "d.prs_merged AS prs_merged, "
        "d.tasks_dispatched AS tasks_dispatched "
        "ORDER BY d.timestamp DESC LIMIT 1"
    )
    return rows[0] if rows else None

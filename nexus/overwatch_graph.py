"""
Overwatch Graph — Overwatch's own memory.

This is NOT the Forgewing graph (that's read-only via neptune_client.py).
This is where Overwatch records its own observations, decisions, actions,
and learnings as it monitors the platform.

Strategy: we share the underlying Neptune Analytics graph (g-1xwjj34141)
to avoid the cost of a second graph, but every Overwatch node uses an
`Overwatch*` label that cannot collide with Forgewing's label namespace.
All writes use MERGE so they're idempotent. Failures are swallowed —
recording must never crash the control plane.

In local mode, an in-memory dict-of-lists stands in for the graph so
tests can exercise the full read/write surface without AWS connectivity.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import AWS_REGION, MODE, OVERWATCH_GRAPH_ID

logger = logging.getLogger("nexus.overwatch_graph")

# --- Local mode in-memory store ----------------------------------------------
# Reentrant lock — record_failure_pattern's local-mode branch holds the lock
# and may then call _create_node, which also tries to acquire it. A plain
# Lock would deadlock; RLock allows the same thread to re-enter.
_lock = threading.RLock()
_local_store: dict[str, list[dict[str, Any]]] = {
    "OverwatchPlatformEvent": [],
    "OverwatchFailurePattern": [],
    "OverwatchHealingAction": [],
    "OverwatchTenantSnapshot": [],
    "OverwatchInvestigation": [],
    "OverwatchHumanDecision": [],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


# --- Production client (lazy) ------------------------------------------------
_client_singleton = None


def _client():
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
    return _client_singleton


def query(cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run an openCypher query and return result rows. Never raises."""
    if MODE != "production":
        logger.debug("[local] overwatch_graph.query: %s", cypher)
        return []
    try:
        resp = _client().execute_query(
            graphIdentifier=OVERWATCH_GRAPH_ID,
            queryString=cypher,
            parameters=parameters or {},
            language="OPEN_CYPHER",
        )
        payload = json.loads(resp["payload"].read())
        return payload.get("results", []) or []
    except Exception:
        logger.exception("overwatch_graph query failed: %s", cypher)
        return []


def _create_node(label: str, props: dict[str, Any]) -> str:
    """
    MERGE a node by its `id` property and SET the rest. Returns the id.
    Local mode appends to _local_store.
    """
    node = dict(props)
    node.setdefault("id", _new_id())
    node.setdefault("created_at", _now_iso())
    if MODE != "production":
        with _lock:
            _local_store.setdefault(label, []).append(node)
        return node["id"]
    set_clauses = ", ".join(f"n.{k} = ${k}" for k in node if k != "id")
    cypher = (
        f"MERGE (n:{label} {{id: $id}}) "
        f"SET {set_clauses} "
        "RETURN n.id AS id"
    )
    rows = query(cypher, node)
    return rows[0].get("id", node["id"]) if rows else node["id"]


# --- Recording API -----------------------------------------------------------
def record_event(
    event_type: str,
    service: str,
    details: dict[str, Any] | None = None,
    severity: str = "info",
) -> str:
    """Record a PlatformEvent and return its id."""
    return _create_node(
        "OverwatchPlatformEvent",
        {
            "event_type": event_type,
            "service": service,
            "details": json.dumps(details or {}),
            "severity": severity,
        },
    )


def record_failure_pattern(
    name: str,
    signature: str,
    diagnosis: str,
    resolution: str,
    *,
    auto_healable: bool = False,
    blast_radius: str = "moderate",
    confidence: float = 0.5,
) -> str:
    """
    MERGE a FailurePattern by name. If it already exists, increment
    occurrence_count and refresh last_seen. New patterns start at the
    given confidence and get learned over time.
    """
    if MODE != "production":
        with _lock:
            patterns = _local_store["OverwatchFailurePattern"]
            existing = next((p for p in patterns if p.get("name") == name), None)
            if existing:
                existing["occurrence_count"] = existing.get("occurrence_count", 0) + 1
                existing["last_seen"] = _now_iso()
                return existing["id"]
            return _create_node(
                "OverwatchFailurePattern",
                {
                    "name": name,
                    "signature": signature,
                    "diagnosis": diagnosis,
                    "resolution": resolution,
                    "auto_healable": auto_healable,
                    "blast_radius": blast_radius,
                    "confidence": confidence,
                    "occurrence_count": 1,
                    "last_seen": _now_iso(),
                    "success_rate": 0.0,
                },
            )
    cypher = (
        "MERGE (p:OverwatchFailurePattern {name: $name}) "
        "ON CREATE SET p.id = $id, p.signature = $signature, "
        "p.diagnosis = $diagnosis, p.resolution = $resolution, "
        "p.auto_healable = $auto_healable, p.blast_radius = $blast_radius, "
        "p.confidence = $confidence, p.occurrence_count = 1, "
        "p.last_seen = $now, p.created_at = $now, p.success_rate = 0.0 "
        "ON MATCH SET p.occurrence_count = coalesce(p.occurrence_count, 0) + 1, "
        "p.last_seen = $now "
        "RETURN p.id AS id"
    )
    rows = query(
        cypher,
        {
            "id": _new_id(),
            "name": name,
            "signature": signature,
            "diagnosis": diagnosis,
            "resolution": resolution,
            "auto_healable": auto_healable,
            "blast_radius": blast_radius,
            "confidence": confidence,
            "now": _now_iso(),
        },
    )
    return rows[0].get("id", "") if rows else ""


def record_healing_action(
    action_type: str,
    target: str,
    blast_radius: str,
    trigger: str,
    outcome: str,
    duration_ms: int | None = None,
) -> str:
    """Record a HealingAction node."""
    return _create_node(
        "OverwatchHealingAction",
        {
            "action_type": action_type,
            "target": target,
            "blast_radius": blast_radius,
            "trigger": trigger,
            "outcome": outcome,
            "duration_ms": duration_ms or 0,
        },
    )


def record_tenant_snapshot(tenant_id: str, statuses: dict[str, Any]) -> str:
    """Snapshot a tenant's health for trending."""
    payload = {"tenant_id": tenant_id, **{k: v for k, v in statuses.items() if v is not None}}
    # Coerce non-scalar values into JSON strings — Neptune properties are scalars.
    for key, value in list(payload.items()):
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value)
    return _create_node("OverwatchTenantSnapshot", payload)


def record_investigation(
    trigger_event: str,
    hypotheses: list[dict[str, Any]],
    conclusion: str,
    confidence: float,
    resolution: str,
    outcome: str,
    duration_ms: int = 0,
) -> str:
    """Record a DiagnosticInvestigation."""
    return _create_node(
        "OverwatchInvestigation",
        {
            "trigger_event": trigger_event,
            "hypotheses": json.dumps(hypotheses),
            "conclusion": conclusion,
            "confidence": confidence,
            "resolution": resolution,
            "outcome": outcome,
            "duration_ms": duration_ms,
        },
    )


def record_human_decision(
    decision_type: str,
    context: str,
    action_taken: str,
    outcome: str,
    automatable: bool = False,
) -> str:
    """Record a HumanDecision — Ian's manual interventions become training data."""
    return _create_node(
        "OverwatchHumanDecision",
        {
            "decision_type": decision_type,
            "context": context,
            "action_taken": action_taken,
            "outcome": outcome,
            "automatable": automatable,
        },
    )


# --- Read API ----------------------------------------------------------------
def get_failure_patterns(min_confidence: float = 0.0) -> list[dict[str, Any]]:
    """Return all FailurePatterns above a confidence threshold."""
    if MODE != "production":
        with _lock:
            return sorted(
                [p for p in _local_store["OverwatchFailurePattern"] if p.get("confidence", 0) >= min_confidence],
                key=lambda p: p.get("occurrence_count", 0),
                reverse=True,
            )
    return query(
        "MATCH (p:OverwatchFailurePattern) WHERE p.confidence >= $min "
        "RETURN p.id AS id, p.name AS name, p.diagnosis AS diagnosis, "
        "p.resolution AS resolution, p.confidence AS confidence, "
        "p.blast_radius AS blast_radius, p.auto_healable AS auto_healable, "
        "p.occurrence_count AS occurrence_count, p.success_rate AS success_rate, "
        "p.last_seen AS last_seen "
        "ORDER BY p.occurrence_count DESC LIMIT 100",
        {"min": min_confidence},
    )


def get_healing_history(hours: int = 24) -> list[dict[str, Any]]:
    """Return healing actions from the last `hours` hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    if MODE != "production":
        with _lock:
            return sorted(
                [a for a in _local_store["OverwatchHealingAction"] if a.get("created_at", "") >= cutoff],
                key=lambda a: a.get("created_at", ""),
                reverse=True,
            )
    return query(
        "MATCH (a:OverwatchHealingAction) WHERE a.created_at >= $cutoff "
        "RETURN a.id AS id, a.action_type AS action_type, a.target AS target, "
        "a.blast_radius AS blast_radius, a.trigger AS trigger, a.outcome AS outcome, "
        "a.duration_ms AS duration_ms, a.created_at AS created_at "
        "ORDER BY a.created_at DESC LIMIT 200",
        {"cutoff": cutoff},
    )


def get_tenant_trend(tenant_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Return TenantSnapshots for a tenant over the last `days` days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if MODE != "production":
        with _lock:
            return sorted(
                [
                    s for s in _local_store["OverwatchTenantSnapshot"]
                    if s.get("tenant_id") == tenant_id and s.get("created_at", "") >= cutoff
                ],
                key=lambda s: s.get("created_at", ""),
            )
    return query(
        "MATCH (s:OverwatchTenantSnapshot) WHERE s.tenant_id = $tid AND s.created_at >= $cutoff "
        "RETURN s.id AS id, s.tenant_id AS tenant_id, s.overall_status AS overall_status, "
        "s.deployment_status AS deployment_status, s.pipeline_status AS pipeline_status, "
        "s.conversation_status AS conversation_status, s.created_at AS created_at "
        "ORDER BY s.created_at",
        {"tid": tenant_id, "cutoff": cutoff},
    )


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent PlatformEvents."""
    if MODE != "production":
        with _lock:
            return sorted(
                _local_store["OverwatchPlatformEvent"],
                key=lambda e: e.get("created_at", ""),
                reverse=True,
            )[:limit]
    return query(
        "MATCH (e:OverwatchPlatformEvent) "
        "RETURN e.id AS id, e.event_type AS event_type, e.service AS service, "
        "e.severity AS severity, e.details AS details, e.created_at AS created_at "
        "ORDER BY e.created_at DESC LIMIT $lim",
        {"lim": limit},
    )


def graph_stats() -> dict[str, int]:
    """Return node counts per Overwatch label."""
    if MODE != "production":
        with _lock:
            return {label: len(nodes) for label, nodes in _local_store.items()}
    stats: dict[str, int] = {}
    for label in _local_store.keys():
        rows = query(f"MATCH (n:{label}) RETURN count(n) AS c")
        stats[label] = int(rows[0].get("c", 0)) if rows else 0
    return stats


def reset_local_store() -> None:
    """Test hook — clear the in-memory store."""
    with _lock:
        for label in _local_store:
            _local_store[label] = []

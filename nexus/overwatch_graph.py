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
    "OverwatchIncident": [],
    "OverwatchCandidatePattern": [],
    # Shared-contract label — Forgewing reads these to render the dashboard
    # ActionBanner. Deliberately unprefixed: this is a cross-system interface,
    # not Overwatch's internal memory.
    "ActionRequired": [],
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


def record_fix_attempt(
    finding_fingerprint: str,
    file_path: str,
    category: str,
    status: str,
    pr_number: int | None = None,
    pr_url: str | None = None,
    reason: str | None = None,
) -> str:
    """Record an OverwatchFixAttempt node — one per FixAgent invocation."""
    return _create_node(
        "OverwatchFixAttempt",
        {
            "finding_fingerprint": finding_fingerprint,
            "file_path": file_path,
            "category": category,
            "status": status,
            "pr_number": pr_number or 0,
            "pr_url": pr_url or "",
            "reason": reason or "",
        },
    )


def record_dogfood_run(
    app_name: str,
    fingerprint: str,
    repo_name: str,
    project_id: str,
    tenant_id: str,
) -> str:
    """Record a new DogfoodRun node in status=pending."""
    return _create_node(
        "OverwatchDogfoodRun",
        {
            "app_name": app_name,
            "fingerprint": fingerprint,
            "repo_name": repo_name,
            "project_id": project_id or "",
            "tenant_id": tenant_id,
            "status": "pending",
            "started_at": _now_iso(),
            "completed_at": "",
            "cleaned_up": "",
        },
    )


def update_dogfood_run(run_id: str, **fields: Any) -> None:
    """Patch fields on an existing DogfoodRun node."""
    if not fields:
        return
    if MODE != "production":
        with _lock:
            for row in _local_store.get("OverwatchDogfoodRun", []):
                if row.get("id") == run_id:
                    row.update(fields)
                    return
        return
    set_clauses = ", ".join(f"d.{k} = ${k}" for k in fields)
    query(
        f"MATCH (d:OverwatchDogfoodRun {{id: $id}}) SET {set_clauses}",
        {"id": run_id, **fields},
    )


def list_dogfood_runs(
    status: str | None = None,
    since_hours: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return DogfoodRun rows, most recent first."""
    if MODE != "production":
        rows = list(_local_store.get("OverwatchDogfoodRun", []))
        if status:
            rows = [r for r in rows if r.get("status") == status]
        rows.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return rows[:limit]
    where = []
    params: dict[str, Any] = {"limit": limit}
    if status:
        where.append("d.status = $status")
        params["status"] = status
    if since_hours is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        where.append("d.started_at >= $cutoff")
        params["cutoff"] = cutoff
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return query(
        f"MATCH (d:OverwatchDogfoodRun) {clause} "
        "RETURN d.id AS id, d.app_name AS app_name, d.fingerprint AS fingerprint, "
        "d.repo_name AS repo_name, d.project_id AS project_id, "
        "d.tenant_id AS tenant_id, d.status AS status, "
        "d.started_at AS started_at, d.completed_at AS completed_at, "
        "d.cleaned_up AS cleaned_up "
        "ORDER BY d.started_at DESC LIMIT $limit",
        params,
    )


def get_dogfood_cursor() -> int:
    """Return the current catalogue cursor position (0 if unset)."""
    if MODE != "production":
        with _lock:
            rows = _local_store.get("OverwatchDogfoodCursor", [])
            return int(rows[0].get("position", 0)) if rows else 0
    rows = query(
        "MATCH (c:OverwatchDogfoodCursor {cursor_id: 'main'}) "
        "RETURN c.position AS position LIMIT 1"
    )
    return int((rows[0].get("position") if rows and isinstance(rows[0], dict) else 0) or 0)


def create_dogfood_batch(batch_id: str, count: int) -> str:
    """Create a DogfoodBatch node tracking a multi-run batch."""
    return _create_node(
        "OverwatchDogfoodBatch",
        {
            "id": batch_id,
            "batch_id": batch_id,
            "requested": count,
            "remaining": count,
            "completed": 0,
            "successes": 0,
            "failures": 0,
            "started_at": _now_iso(),
        },
    )


def get_active_batch() -> dict[str, Any] | None:
    """Return the active DogfoodBatch (remaining > 0), or None."""
    if MODE != "production":
        with _lock:
            for row in _local_store.get("OverwatchDogfoodBatch", []):
                if int(row.get("remaining") or 0) > 0:
                    return dict(row)
        return None
    rows = query(
        "MATCH (b:OverwatchDogfoodBatch) WHERE b.remaining > 0 "
        "RETURN b.batch_id AS batch_id, b.requested AS requested, "
        "b.remaining AS remaining, b.completed AS completed, "
        "b.successes AS successes, b.failures AS failures, "
        "b.started_at AS started_at "
        "ORDER BY b.started_at DESC LIMIT 1"
    )
    return rows[0] if rows and isinstance(rows[0], dict) else None


def decrement_batch(batch_id: str, success: bool) -> None:
    """Decrement remaining, bump completed/successes/failures counts."""
    if MODE != "production":
        with _lock:
            for row in _local_store.get("OverwatchDogfoodBatch", []):
                if row.get("batch_id") == batch_id:
                    row["remaining"] = max(0, int(row.get("remaining") or 0) - 1)
                    row["completed"] = int(row.get("completed") or 0) + 1
                    if success:
                        row["successes"] = int(row.get("successes") or 0) + 1
                    else:
                        row["failures"] = int(row.get("failures") or 0) + 1
                    return
        return
    key = "successes" if success else "failures"
    query(
        "MATCH (b:OverwatchDogfoodBatch {batch_id: $bid}) "
        f"SET b.remaining = b.remaining - 1, b.completed = b.completed + 1, "
        f"b.{key} = b.{key} + 1",
        {"bid": batch_id},
    )


def get_dogfood_config() -> dict[str, Any]:
    """Read the DogfoodConfig singleton — activation state set by the UI."""
    if MODE != "production":
        with _lock:
            rows = _local_store.get("OverwatchDogfoodConfig", [])
            return dict(rows[0]) if rows else {}
    rows = query(
        "MATCH (c:OverwatchDogfoodConfig {config_id: 'main'}) "
        "RETURN c.enabled AS enabled, c.activated_by AS activated_by, "
        "c.activated_at AS activated_at, c.paused_at AS paused_at, "
        "c.tenant_id AS tenant_id LIMIT 1"
    )
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def set_dogfood_config(enabled: bool, activated_by: str = "ui",
                       tenant_id: str | None = None) -> None:
    """Write the DogfoodConfig singleton."""
    ts = _now_iso()
    if MODE != "production":
        with _lock:
            rows = _local_store.setdefault("OverwatchDogfoodConfig", [])
            if rows:
                rows[0]["enabled"] = enabled
                rows[0]["activated_by"] = activated_by
                if tenant_id is not None:
                    rows[0]["tenant_id"] = tenant_id
                if enabled:
                    rows[0]["activated_at"] = ts
                else:
                    rows[0]["paused_at"] = ts
            else:
                rows.append({"config_id": "main", "id": _new_id(),
                             "enabled": enabled, "activated_by": activated_by,
                             "tenant_id": tenant_id or "",
                             "activated_at": ts if enabled else "",
                             "paused_at": "" if enabled else ts})
        return
    tid_clause = ", c.tenant_id = $tid" if tenant_id is not None else ""
    params: dict[str, Any] = {"ts": ts, "by": activated_by}
    if tenant_id is not None:
        params["tid"] = tenant_id
    if enabled:
        query(
            "MERGE (c:OverwatchDogfoodConfig {config_id: 'main'}) "
            f"SET c.enabled = true, c.activated_at = $ts, c.activated_by = $by{tid_clause}",
            params,
        )
    else:
        query(
            "MERGE (c:OverwatchDogfoodConfig {config_id: 'main'}) "
            f"SET c.enabled = false, c.paused_at = $ts{tid_clause}",
            params,
        )


def get_dogfood_schedule() -> dict[str, Any]:
    """Read the DogfoodSchedule singleton — auto-batch config."""
    if MODE != "production":
        with _lock:
            rows = _local_store.get("OverwatchDogfoodSchedule", [])
            return dict(rows[0]) if rows else {}
    rows = query(
        "MATCH (s:OverwatchDogfoodSchedule {schedule_id: 'main'}) "
        "RETURN s.runs_per_day AS runs_per_day, s.enabled AS enabled, "
        "s.next_run AS next_run LIMIT 1"
    )
    return rows[0] if rows and isinstance(rows[0], dict) else {}


def set_dogfood_schedule(runs_per_day: int, enabled: bool) -> None:
    """Write the DogfoodSchedule singleton."""
    ts = _now_iso()
    if MODE != "production":
        with _lock:
            rows = _local_store.setdefault("OverwatchDogfoodSchedule", [])
            data = {"schedule_id": "main", "id": _new_id(),
                    "runs_per_day": runs_per_day, "enabled": enabled,
                    "updated_at": ts, "next_run": ""}
            if rows:
                rows[0].update(data)
            else:
                rows.append(data)
        return
    query(
        "MERGE (s:OverwatchDogfoodSchedule {schedule_id: 'main'}) "
        "SET s.runs_per_day = $rpd, s.enabled = $en, s.updated_at = $ts",
        {"rpd": runs_per_day, "en": enabled, "ts": ts},
    )


def advance_dogfood_cursor() -> int:
    """Advance the catalogue cursor by 1. Returns the NEW position."""
    new_pos = get_dogfood_cursor() + 1
    if MODE != "production":
        with _lock:
            rows = _local_store.setdefault("OverwatchDogfoodCursor", [])
            if rows:
                rows[0]["position"] = new_pos
            else:
                rows.append({"cursor_id": "main", "position": new_pos, "id": _new_id()})
        return new_pos
    query(
        "MERGE (c:OverwatchDogfoodCursor {cursor_id: 'main'}) "
        "SET c.position = $pos, c.updated_at = $ts",
        {"pos": new_pos, "ts": _now_iso()},
    )
    return new_pos


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


# --- Incident lifecycle -------------------------------------------------------
# The antifragile loop: detect → acknowledge → resolve → learn → prevent
#
# An incident is "open" until resolved_at is set. Each source (daemon, ci,
# tenant:X) can have at most one open incident at a time — MERGE by source.

def open_incident(
    source: str,
    incident_type: str,
    root_cause: str = "",
    patterns_matched: list[str] | None = None,
) -> str:
    """
    Create or re-open an incident for `source`. Returns the incident id.
    If an open incident already exists for this source, returns its id
    without creating a duplicate.
    """
    now = _now_iso()
    if MODE != "production":
        with _lock:
            existing = next(
                (i for i in _local_store["OverwatchIncident"]
                 if i.get("source") == source and not i.get("resolved_at")),
                None,
            )
            if existing:
                return existing["id"]
            node = {
                "id": _new_id(),
                "source": source,
                "type": incident_type,
                "detected_at": now,
                "acknowledged_at": None,
                "resolved_at": None,
                "duration_seconds": None,
                "auto_healed": False,
                "patterns_matched": json.dumps(patterns_matched or []),
                "healing_actions": json.dumps([]),
                "root_cause": root_cause,
                "prevention_added": False,
                "created_at": now,
            }
            _local_store["OverwatchIncident"].append(node)
            return node["id"]
    rows = query(
        "MERGE (i:OverwatchIncident {source: $src, resolved_at: ''}) "
        "ON CREATE SET i.id = $id, i.type = $type, i.detected_at = $now, "
        "i.root_cause = $root, i.patterns_matched = $patterns, "
        "i.healing_actions = '[]', i.auto_healed = false, "
        "i.prevention_added = false, i.created_at = $now "
        "RETURN i.id AS id",
        {
            "src": source,
            "id": _new_id(),
            "type": incident_type,
            "now": now,
            "root": root_cause,
            "patterns": json.dumps(patterns_matched or []),
        },
    )
    return rows[0].get("id", "") if rows else ""


def acknowledge_incident(source: str, action_taken: str) -> None:
    """Mark the open incident for `source` as acknowledged (first action taken)."""
    now = _now_iso()
    if MODE != "production":
        with _lock:
            inc = next(
                (i for i in _local_store["OverwatchIncident"]
                 if i.get("source") == source and not i.get("resolved_at")),
                None,
            )
            if inc and not inc.get("acknowledged_at"):
                inc["acknowledged_at"] = now
                actions = json.loads(inc.get("healing_actions", "[]"))
                actions.append(action_taken)
                inc["healing_actions"] = json.dumps(actions)
        return
    query(
        "MATCH (i:OverwatchIncident {source: $src}) "
        "WHERE i.resolved_at = '' AND (i.acknowledged_at IS NULL OR i.acknowledged_at = '') "
        "SET i.acknowledged_at = $now",
        {"src": source, "now": now},
    )


def resolve_incident(source: str, auto_healed: bool = False) -> dict[str, Any] | None:
    """
    Close the open incident for `source`. Returns the resolved incident
    dict with computed duration, or None if no open incident exists.
    """
    now = _now_iso()
    if MODE != "production":
        with _lock:
            inc = next(
                (i for i in _local_store["OverwatchIncident"]
                 if i.get("source") == source and not i.get("resolved_at")),
                None,
            )
            if not inc:
                return None
            inc["resolved_at"] = now
            inc["auto_healed"] = auto_healed
            detected = inc.get("detected_at", now)
            try:
                d0 = datetime.fromisoformat(detected)
                d1 = datetime.fromisoformat(now)
                inc["duration_seconds"] = round((d1 - d0).total_seconds(), 1)
            except Exception:
                inc["duration_seconds"] = 0
            return dict(inc)
    rows = query(
        "MATCH (i:OverwatchIncident {source: $src}) "
        "WHERE i.resolved_at = '' "
        "SET i.resolved_at = $now, i.auto_healed = $healed "
        "RETURN i.id AS id, i.detected_at AS detected_at, "
        "i.acknowledged_at AS acknowledged_at, i.type AS type, "
        "i.source AS source, i.root_cause AS root_cause",
        {"src": source, "now": now, "healed": auto_healed},
    )
    return rows[0] if rows else None


def get_open_incidents() -> list[dict[str, Any]]:
    """All currently-open incidents."""
    if MODE != "production":
        with _lock:
            return [i for i in _local_store["OverwatchIncident"] if not i.get("resolved_at")]
    return query(
        "MATCH (i:OverwatchIncident) WHERE i.resolved_at = '' "
        "RETURN i.id AS id, i.source AS source, i.type AS type, "
        "i.detected_at AS detected_at, i.acknowledged_at AS acknowledged_at, "
        "i.root_cause AS root_cause, i.patterns_matched AS patterns_matched "
        "ORDER BY i.detected_at DESC"
    )


def get_resolved_incidents(hours: int = 24) -> list[dict[str, Any]]:
    """Resolved incidents from the last `hours` hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    if MODE != "production":
        with _lock:
            return [
                i for i in _local_store["OverwatchIncident"]
                if i.get("resolved_at") and i.get("resolved_at", "") >= cutoff
            ]
    return query(
        "MATCH (i:OverwatchIncident) WHERE i.resolved_at <> '' AND i.resolved_at >= $cutoff "
        "RETURN i.id AS id, i.source AS source, i.type AS type, "
        "i.detected_at AS detected_at, i.acknowledged_at AS acknowledged_at, "
        "i.resolved_at AS resolved_at, i.duration_seconds AS duration_seconds, "
        "i.auto_healed AS auto_healed, i.root_cause AS root_cause, "
        "i.patterns_matched AS patterns_matched, i.prevention_added AS prevention_added "
        "ORDER BY i.resolved_at DESC",
        {"cutoff": cutoff},
    )


def record_candidate_pattern(data: dict[str, Any]) -> str:
    """Persist or update a CandidatePattern node (MERGE by name)."""
    name = data.get("name", "")
    now = _now_iso()
    if MODE != "production":
        with _lock:
            existing = next(
                (n for n in _local_store["OverwatchCandidatePattern"] if n.get("name") == name),
                None,
            )
            if existing:
                existing.update({k: v for k, v in data.items() if v is not None})
                existing["updated_at"] = now
                return existing.get("id", name)
            node = {"id": f"candidate-{name}", **data, "created_at": data.get("created_at", now), "updated_at": now}
            _local_store["OverwatchCandidatePattern"].append(node)
            return node["id"]
    props = {k: v for k, v in data.items() if v is not None}
    props["updated_at"] = now
    # Coerce complex values to JSON strings
    for key in ("heal_kwargs_template",):
        if key in props and isinstance(props[key], dict):
            props[key] = json.dumps(props[key])
    query(
        "MERGE (c:OverwatchCandidatePattern {name: $name}) SET c += $props",
        {"name": name, "props": props},
    )
    return f"candidate-{name}"


def get_candidate_patterns() -> list[dict[str, Any]]:
    """Return all CandidatePattern nodes."""
    if MODE != "production":
        with _lock:
            return list(_local_store["OverwatchCandidatePattern"])
    return query(
        "MATCH (c:OverwatchCandidatePattern) "
        "RETURN c.name AS name, c.signature AS signature, "
        "c.match_source AS match_source, c.match_action AS match_action, "
        "c.heal_capability AS heal_capability, c.diagnosis AS diagnosis, "
        "c.resolution AS resolution, c.blast_radius AS blast_radius, "
        "c.confidence AS confidence, c.success_count AS success_count, "
        "c.failure_count AS failure_count, c.graduated AS graduated, "
        "c.created_at AS created_at "
        "ORDER BY c.created_at DESC"
    )


def write_tenant_action(tenant_id: str, action_type: str, props: dict[str, Any]) -> str:
    """
    Upsert an ActionRequired node, keyed on (tenant_id, action_type).

    Shared-contract label: Forgewing reads these to render the dashboard
    ActionBanner. MERGE semantics mean repeated calls refresh the node
    rather than creating duplicates.
    """
    now = _now_iso()
    payload = {
        "tenant_id": tenant_id,
        "action_type": action_type,
        "dismissed": False,
        "updated_at": now,
        **{k: v for k, v in props.items() if v is not None},
    }
    payload.setdefault("created_at", now)
    if MODE != "production":
        with _lock:
            existing = next(
                (n for n in _local_store["ActionRequired"]
                 if n.get("tenant_id") == tenant_id and n.get("action_type") == action_type),
                None,
            )
            if existing:
                existing.update(payload)
                return existing.get("id", "")
            node = {"id": _new_id(), **payload}
            _local_store["ActionRequired"].append(node)
            return node["id"]
    query(
        "MERGE (a:ActionRequired {tenant_id: $tenant_id, action_type: $action_type}) "
        "ON CREATE SET a.id = $id, a += $props "
        "ON MATCH SET a += $props",
        {
            "tenant_id": tenant_id,
            "action_type": action_type,
            "id": _new_id(),
            "props": payload,
        },
    )
    return f"{tenant_id}:{action_type}"


def clear_tenant_action(tenant_id: str, action_type: str) -> bool:
    """Delete an ActionRequired node. Returns True if something was removed."""
    if MODE != "production":
        with _lock:
            before = len(_local_store["ActionRequired"])
            _local_store["ActionRequired"] = [
                n for n in _local_store["ActionRequired"]
                if not (n.get("tenant_id") == tenant_id and n.get("action_type") == action_type)
            ]
            return len(_local_store["ActionRequired"]) < before
    query(
        "MATCH (a:ActionRequired {tenant_id: $tenant_id, action_type: $action_type}) "
        "DETACH DELETE a",
        {"tenant_id": tenant_id, "action_type": action_type},
    )
    return True


def get_tenant_actions(tenant_id: str | None = None) -> list[dict[str, Any]]:
    """Return ActionRequired nodes for a tenant (or all if None)."""
    if MODE != "production":
        with _lock:
            rows = list(_local_store["ActionRequired"])
        if tenant_id:
            rows = [r for r in rows if r.get("tenant_id") == tenant_id]
        return rows
    if tenant_id:
        return query(
            "MATCH (a:ActionRequired {tenant_id: $tid}) "
            "RETURN a.tenant_id AS tenant_id, a.action_type AS action_type, "
            "a.severity AS severity, a.title AS title, a.message AS message, "
            "a.button_label AS button_label, a.destination AS destination, "
            "a.category AS category, a.dismissed AS dismissed, "
            "a.created_at AS created_at, a.updated_at AS updated_at",
            {"tid": tenant_id},
        )
    return query(
        "MATCH (a:ActionRequired) "
        "RETURN a.tenant_id AS tenant_id, a.action_type AS action_type, "
        "a.severity AS severity, a.title AS title, a.dismissed AS dismissed, "
        "a.created_at AS created_at"
    )


def reset_local_store() -> None:
    """Test hook — clear the in-memory store."""
    with _lock:
        for label in _local_store:
            _local_store[label] = []

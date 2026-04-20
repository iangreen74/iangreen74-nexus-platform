"""Pipeline Events query endpoint — read-only Loop 1 telemetry feed.

Events are written by nexus/sensors/pipeline_event_sensor.py which polls
SQS (forgewing-pipeline-events). Each becomes a PipelineEvent node via
overwatch_graph.record_pipeline_event.

GET /api/pipeline-events?event_type=&tenant_id=&since=&limit=
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from nexus import neptune_client

logger = logging.getLogger("nexus.dashboard.pipeline_events")

router = APIRouter(prefix="/api/pipeline-events", tags=["pipeline-events"])

KNOWN_EVENT_TYPES = [
    "ci_deploy_completed", "ci_deploy_failed",
    "ci_daemon_deploy_completed", "ci_daemon_deploy_failed",
    "ci_gate_overridden",
]


def _query(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        result = neptune_client.query(cypher, params or {})
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("pipeline-events query failed: %s", e)
        return []


@router.get("")
async def list_pipeline_events(
    event_type: str | None = Query(None),
    tenant_id: str | None = Query(None),
    since: str | None = Query(None, description="ISO-8601 lower bound"),
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    """Recent PipelineEvent nodes, filterable."""
    if event_type is not None and event_type not in KNOWN_EVENT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"event_type must be one of {KNOWN_EVENT_TYPES}")

    where: list[str] = []
    params: dict[str, Any] = {"limit": limit}
    if event_type:
        where.append("e.event_type = $event_type")
        params["event_type"] = event_type
    if tenant_id:
        where.append("e.tenant_id = $tenant_id")
        params["tenant_id"] = tenant_id
    if since:
        where.append("e.emitted_at >= $since")
        params["since"] = since
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    cypher = (
        f"MATCH (e:PipelineEvent){clause} "
        "RETURN e.id AS event_id, e.event_type AS event_type, "
        "e.emitted_at AS emitted_at, e.tenant_id AS tenant_id, "
        "e.project_id AS project_id, e.correlation_id AS correlation_id, "
        "e.payload_json AS payload_json, e.recorded_at AS recorded_at "
        "ORDER BY e.emitted_at DESC LIMIT $limit"
    )

    rows = _query(cypher, params)
    events = [{
        "event_id": r.get("event_id"),
        "event_type": r.get("event_type"),
        "emitted_at": r.get("emitted_at"),
        "tenant_id": r.get("tenant_id"),
        "project_id": r.get("project_id"),
        "correlation_id": r.get("correlation_id"),
        "recorded_at": r.get("recorded_at"),
    } for r in rows]

    type_counts: dict[str, int] = {}
    for ev in events:
        t = ev["event_type"] or "unknown"
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "filters": {"event_type": event_type, "tenant_id": tenant_id,
                     "since": since, "limit": limit},
        "count": len(events),
        "type_counts": type_counts,
        "known_event_types": KNOWN_EVENT_TYPES,
        "events": events,
    }

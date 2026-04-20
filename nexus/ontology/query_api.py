"""Loom query endpoints — read-only views into the Startup Ontology.

Separate from routes.py (which hosts propose_object/update_object actions)
to maintain read/write separation. Mounted on /api/ontology alongside the
action router — no path conflicts because methods differ.

Endpoints:
  GET /api/ontology/summary — counts per type per tenant
  GET /api/ontology/tenants/{tenant_id}/objects — recent objects
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from nexus import neptune_client

logger = logging.getLogger("nexus.ontology.query")

router = APIRouter(tags=["ontology"])

OBJECT_TYPES = ["Feature", "Decision", "Hypothesis"]
LINK_TYPES = ["motivates", "supersedes", "validates"]


def _query(cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        result = neptune_client.query(cypher, params or {})
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("ontology query failed: %s", e)
        return []


@router.get("/summary")
async def ontology_summary() -> dict[str, Any]:
    """Counts per object type per tenant, plus link counts."""
    tenants: dict[str, dict[str, Any]] = {}

    for obj_type in OBJECT_TYPES:
        rows = _query(
            f"MATCH (o:{obj_type}) WHERE o.tenant_id IS NOT NULL "
            f"RETURN o.tenant_id AS tid, count(o) AS n"
        )
        for row in rows:
            tid = row.get("tid")
            if not tid:
                continue
            t = tenants.setdefault(tid, {
                "tenant_id": tid,
                "objects": {o: 0 for o in OBJECT_TYPES},
                "links": {l: 0 for l in LINK_TYPES},
            })
            t["objects"][obj_type] = int(row.get("n") or 0)

    for link_type in LINK_TYPES:
        rows = _query(
            f"MATCH (a)-[r:{link_type}]->(b) WHERE r.tenant_id IS NOT NULL "
            f"RETURN r.tenant_id AS tid, count(r) AS n"
        )
        for row in rows:
            tid = row.get("tid")
            if not tid:
                continue
            t = tenants.setdefault(tid, {
                "tenant_id": tid,
                "objects": {o: 0 for o in OBJECT_TYPES},
                "links": {l: 0 for l in LINK_TYPES},
            })
            t["links"][link_type] = int(row.get("n") or 0)

    tenants_list = []
    total_objects = total_links = 0
    for tid, t in sorted(tenants.items()):
        t["total_objects"] = sum(t["objects"].values())
        t["total_links"] = sum(t["links"].values())
        total_objects += t["total_objects"]
        total_links += t["total_links"]
        tenants_list.append(t)

    return {
        "tenants": tenants_list,
        "totals": {"objects": total_objects, "links": total_links},
        "object_types": OBJECT_TYPES,
        "link_types": LINK_TYPES,
    }


@router.get("/tenants/{tenant_id}/objects")
async def ontology_tenant_objects(
    tenant_id: str,
    type: str | None = Query(None, description="Filter by object type"),
    limit: int = Query(20, ge=1, le=200),
) -> dict[str, Any]:
    """Recent Loom objects for a tenant, optionally filtered by type."""
    if type is not None and type not in OBJECT_TYPES:
        raise HTTPException(status_code=400, detail=f"type must be one of {OBJECT_TYPES}")

    types_to_query = [type] if type else OBJECT_TYPES
    items: list[dict[str, Any]] = []
    for t in types_to_query:
        rows = _query(
            f"MATCH (o:{t}) WHERE o.tenant_id = $tid "
            f"RETURN o.id AS object_id, o.name AS title, "
            f"o.created_at AS created_at, o.updated_at AS updated_at, "
            f"o.version_id AS version_id "
            f"ORDER BY o.updated_at DESC LIMIT $lim",
            {"tid": tenant_id, "lim": limit},
        )
        for row in rows:
            items.append({
                "object_id": row.get("object_id"),
                "object_type": t,
                "title": row.get("title"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "version_id": row.get("version_id"),
            })

    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {
        "tenant_id": tenant_id,
        "type_filter": type,
        "count": len(items[:limit]),
        "objects": items[:limit],
    }

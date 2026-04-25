"""Tool 5 — query_engineering_ontology: wraps Track E's ontology service.

In NEXUS_MODE != 'production', Track E's _local_store is the backing —
no Postgres or Neptune connection required for tests.
"""
from __future__ import annotations

from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolNotFound, ToolUnknown,
)


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["get_object", "list_objects_by_type", "query_neighbors"],
        },
        "object_id": {"type": "string"},
        "object_type": {"type": "string",
                        "description": "One of the 13 V2 node types (NodeType.values())."},
        "version": {"type": "integer",
                    "description": "Specific version for get_object; current if absent."},
        "limit": {"type": "integer", "description": "default 50, max 200"},
    },
    "required": ["operation"],
}


def handler(**params: Any) -> dict:
    op = params["operation"]
    limit = max(1, min(int(params.get("limit") or 50), 200))
    if op == "get_object":
        oid = params.get("object_id")
        if not oid:
            raise ToolUnknown("get_object requires `object_id`")
        from nexus.overwatch_v2.ontology import get_object
        row = get_object(oid, version=params.get("version"))
        if row is None:
            raise ToolNotFound(f"object not found: {oid}")
        return {"object": _coerce(row)}
    if op == "list_objects_by_type":
        otype = params.get("object_type")
        if not otype:
            raise ToolUnknown("list_objects_by_type requires `object_type`")
        from nexus.overwatch_v2.ontology import list_objects_by_type
        rows = list_objects_by_type(otype, limit=limit) or []
        return {"object_type": otype, "objects": [_coerce(r) for r in rows]}
    if op == "query_neighbors":
        oid = params.get("object_id")
        if not oid:
            raise ToolUnknown("query_neighbors requires `object_id`")
        from nexus.overwatch_v2.ontology import get_object, query
        anchor = get_object(oid)
        if anchor is None:
            raise ToolNotFound(f"object not found: {oid}")
        cypher = (
            "MATCH (n {id: $id})-[r]-(m) "
            "RETURN type(r) AS edge_type, properties(m) AS neighbor LIMIT $limit"
        )
        rows = query(cypher, {"id": oid, "limit": limit}) or []
        return {"object_id": oid, "anchor_type": anchor.get("object_type"),
                "neighbors": [_coerce(r) for r in rows]}
    raise ToolUnknown(f"unknown operation: {op!r}")


def _coerce(row: Any) -> dict:
    if isinstance(row, dict):
        return {k: v for k, v in row.items() if not str(k).startswith("_")}
    return {"value": row}


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="query_engineering_ontology",
        description=(
            "Read V2 engineering ontology objects (Track E's surface). "
            "Operations: get_object (by id, optional version), "
            "list_objects_by_type (one of 13 node types), "
            "query_neighbors (1-hop graph traversal)."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

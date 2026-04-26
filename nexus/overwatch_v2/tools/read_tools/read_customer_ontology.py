"""Phase 1 read_customer_ontology — tenant-scoped Forgewing graph reads.

Cypher reads against the Forgewing Neptune Analytics graph
(g-1xwjj34141) scoped to a single tenant. Tenant-scoped via WHERE
clause on `tenant_id` (Forgewing's per-tenant property).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from nexus.overwatch_v2.tools.read_tools._tenant_scope import (
    require_tenant_id,
    write_audit_event,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


TOOL_NAME = "read_customer_ontology"
FORGEWING_GRAPH_ID = "g-1xwjj34141"

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {
            "type": "string",
            "description": "Full tenant ID (e.g. forge-1dba4143ca24ed1f).",
        },
    },
    "required": ["tenant_id"],
}


def _query(client, cypher: str, params: dict) -> list[dict]:
    """Execute a parameterised Cypher read against the Forgewing graph."""
    resp = client.execute_query(
        graphIdentifier=FORGEWING_GRAPH_ID,
        queryString=cypher,
        language="OPEN_CYPHER",
        parameters=params,
    )
    payload = resp.get("payload")
    if hasattr(payload, "read"):
        import json as _json
        text = payload.read()
        data = _json.loads(text) if text else {}
    elif isinstance(payload, (bytes, str)):
        import json as _json
        data = _json.loads(payload) if payload else {}
    else:
        data = payload or {}
    return data.get("results", []) or []


def handler(**params: Any) -> dict:
    tenant_id = require_tenant_id(params.get("tenant_id"))
    from nexus.aws_client import _client
    try:
        client = _client("neptune-graph")
    except Exception as e:
        raise map_boto_error(e) from e

    out: dict[str, Any] = {
        "tenant_id": tenant_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "graph_id": FORGEWING_GRAPH_ID,
    }
    counts: dict[str, int] = {}
    samples: dict[str, list] = {}

    queries = [
        ("tenant_node",
         "MATCH (t:Tenant {tenant_id: $tid}) RETURN t LIMIT 1"),
        ("project_count",
         "MATCH (p {tenant_id: $tid}) WHERE p:Project OR p:Goal "
         "RETURN labels(p) AS label, count(*) AS n"),
        ("recent_tasks",
         "MATCH (t:Task {tenant_id: $tid}) "
         "RETURN t.task_id AS task_id, t.status AS status, "
         "t.created_at AS created_at "
         "ORDER BY t.created_at DESC LIMIT 10"),
        ("recent_pipeline_events",
         "MATCH (e:PipelineEvent {tenant_id: $tid}) "
         "RETURN e.event_type AS event_type, e.timestamp AS timestamp, "
         "e.status AS status "
         "ORDER BY e.timestamp DESC LIMIT 10"),
    ]

    for label, cypher in queries:
        try:
            rows = _query(client, cypher, {"tid": tenant_id})
            samples[label] = rows
            counts[label] = len(rows)
        except Exception as e:
            samples[label] = []
            counts[label] = 0
            out.setdefault("query_errors", {})[label] = str(e)[:300]

    out["counts"] = counts
    out["samples"] = samples

    write_audit_event(
        tenant_id=tenant_id,
        tool_name=TOOL_NAME,
        resource_arns=[
            f"arn:aws:neptune-graph:us-east-1:418295677815:graph/{FORGEWING_GRAPH_ID}"
        ],
        result_count=sum(counts.values()),
    )
    return out


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name=TOOL_NAME,
        description=(
            "Read tenant-scoped ontology data from the Forgewing Neptune "
            "Analytics graph: tenant node, projects/goals, recent tasks, "
            "recent pipeline events. WHERE clause enforces tenant_id scope."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

"""Tool 11 — read_aria_conversations: ConversationMessage nodes per tenant.

Reads from the Forgewing Neptune Analytics graph (g-1xwjj34141) via
openCypher with `tenant_id` filter. Same Path γ guardrails — but the
scope assertion is value-equality on tenant_id, not name-prefix
(conversations are filter-by-column, not filter-by-resource-name).
"""
from __future__ import annotations

import json
from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)
from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    CrossTenantLeakageError, _audit_cross_tenant_call, _validate_tenant_id,
)


FORGEWING_GRAPH_ID = "g-1xwjj34141"
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "tenant_id": {"type": "string", "description": "Forgewing tenant ID"},
        "limit": {"type": "integer", "description": f"Default {DEFAULT_LIMIT}, max {MAX_LIMIT}"},
    },
    "required": ["tenant_id"],
}


def _client():
    from nexus.aws_client import _client as factory
    return factory("neptune-graph")


def _assert_value_scoped(
    rows: list[dict[str, Any]], tenant_id: str, field: str = "tenant_id",
) -> None:
    """Value-equality version of the scope assertion. Every row must
    carry tenant_id matching the request, or it's leakage.
    """
    for r in rows:
        observed = (r or {}).get(field, "")
        if observed != tenant_id:
            raise CrossTenantLeakageError(
                f"CROSS-TENANT LEAKAGE: tool requested tenant_id={tenant_id} "
                f"but row has {field}={observed!r}. Refusing to return data."
            )


def handler(**params: Any) -> dict[str, Any]:
    tenant_id = params.get("tenant_id", "")
    limit = max(1, min(int(params.get("limit") or DEFAULT_LIMIT), MAX_LIMIT))
    try:
        _validate_tenant_id(tenant_id)
    except ValueError as e:
        raise ToolUnknown(str(e)) from e
    cypher = (
        "MATCH (m:ConversationMessage {tenant_id: $tid}) "
        "RETURN m.tenant_id AS tenant_id, m.message_id AS message_id, "
        "m.role AS role, m.content AS content, m.timestamp AS timestamp, "
        "m.project_id AS project_id "
        "ORDER BY m.timestamp DESC LIMIT $lim"
    )
    rows: list[dict[str, Any]] = []
    try:
        client = _client()
        resp = client.execute_query(
            graphIdentifier=FORGEWING_GRAPH_ID,
            queryString=cypher,
            parameters={"tid": tenant_id, "lim": limit},
            language="OPEN_CYPHER",
        )
        payload = json.loads(resp["payload"].read())
        rows = payload.get("results", []) or []
    except Exception as e:
        _audit_cross_tenant_call(
            tenant_id, "read_aria_conversations", [], 0, error=str(e),
        )
        if isinstance(e, (ToolUnknown, AssertionError)):
            raise
        raise map_boto_error(e) from e
    try:
        _assert_value_scoped(rows, tenant_id, "tenant_id")
    except AssertionError as e:
        _audit_cross_tenant_call(
            tenant_id, "read_aria_conversations", [], len(rows), error=str(e),
        )
        raise
    _audit_cross_tenant_call(
        tenant_id, "read_aria_conversations",
        [f"forgewing-graph:{FORGEWING_GRAPH_ID}"], len(rows),
    )
    truncated = [
        {
            "message_id": r.get("message_id"),
            "role": r.get("role"),
            "content": (r.get("content") or "")[:2000],
            "timestamp": r.get("timestamp"),
            "project_id": r.get("project_id"),
        }
        for r in rows
    ]
    from datetime import datetime, timezone
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "conversation_messages": truncated,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_aria_conversations",
        description=(
            "Phase 0c cross-tenant read. Returns recent ARIA "
            "ConversationMessage nodes from the Forgewing Neptune graph "
            "filtered by tenant_id. Asserts every row's tenant_id matches "
            "the request; audits."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

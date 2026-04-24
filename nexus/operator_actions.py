"""Surgeon — curated operator actions for tenant remediation.

Every invocation writes an :OperatorAction node to Neptune for forensic
audit. Operations are bounded and idempotent — no arbitrary graph access.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nexus.config import MODE

log = logging.getLogger(__name__)


@dataclass
class OperatorActionResult:
    project_id: str
    created: bool
    audit_id: str


def _graph_query(cypher: str, params: dict | None = None) -> list[dict]:
    """Query Neptune via overwatch_graph. Returns [] on error."""
    try:
        from nexus.overwatch_graph import query
        return query(cypher, params or {}) or []
    except Exception as e:
        log.warning("graph query failed: %s", e)
        return []


def _record_operator_action(
    action_type: str,
    operator_id: str,
    tenant_id: str,
    result: dict[str, Any],
    mutated_nodes: list[str] | None = None,
) -> str:
    """Write an :OperatorAction node to Neptune. Returns audit_id."""
    audit_id = f"op-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    _graph_query(
        "MERGE (a:OperatorAction {audit_id: $aid}) "
        "SET a.action_type = $atype, a.operator_id = $oid, "
        "a.tenant_id = $tid, a.result = $result, "
        "a.mutated_nodes = $nodes, a.created_at = $now",
        {
            "aid": audit_id,
            "atype": action_type,
            "oid": operator_id,
            "tid": tenant_id,
            "result": str(result),
            "nodes": str(mutated_nodes or []),
            "now": now,
        },
    )
    log.info("OperatorAction %s: %s on %s by %s",
             audit_id, action_type, tenant_id[:12], operator_id)
    return audit_id


def create_default_project(
    tenant_id: str,
    operator_id: str = "ian",
) -> OperatorActionResult:
    """Create a missing default Project node for a tenant.

    Idempotent: returns created=False if Project already exists.
    Raises ValueError if Tenant node doesn't exist.
    """
    # 1. Verify Tenant exists
    tenant_rows = _graph_query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "RETURN t.company_name AS company, t.repo_url AS repo",
        {"tid": tenant_id},
    )
    if not tenant_rows:
        if MODE != "production":
            tenant_rows = [{"company": "mock-co", "repo": "mock/repo"}]
        else:
            raise ValueError(f"Tenant {tenant_id} not found")

    tenant = tenant_rows[0] if tenant_rows else {}
    company = tenant.get("company") or "default"
    repo_url = tenant.get("repo_url") or tenant.get("repo") or ""

    # 2. Check if Project already exists
    existing = _graph_query(
        "MATCH (p:Project {project_id: $pid, tenant_id: $tid}) "
        "RETURN p.project_id AS pid",
        {"pid": tenant_id, "tid": tenant_id},
    )
    if existing:
        audit_id = _record_operator_action(
            "create_default_project", operator_id, tenant_id,
            {"created": False, "reason": "already_exists"},
        )
        return OperatorActionResult(
            project_id=tenant_id, created=False, audit_id=audit_id,
        )

    # 3. Create the Project node
    now = datetime.now(timezone.utc).isoformat()
    _graph_query(
        "MERGE (p:Project {project_id: $pid, tenant_id: $tid}) "
        "SET p.name = $name, p.repo_url = $repo, "
        "p.status = 'active', p.is_default = true, "
        "p.created_at = $now, p.created_by = $by",
        {
            "pid": tenant_id,
            "tid": tenant_id,
            "name": company,
            "repo": repo_url,
            "now": now,
            "by": f"operator:{operator_id}",
        },
    )

    # 4. Record audit
    audit_id = _record_operator_action(
        "create_default_project", operator_id, tenant_id,
        {"created": True, "project_id": tenant_id},
        mutated_nodes=[f"Project:{tenant_id}"],
    )

    return OperatorActionResult(
        project_id=tenant_id, created=True, audit_id=audit_id,
    )


# Re-export from operator_repair for backward compatibility
from nexus.operator_repair import PROJECT_SCOPED_LABELS, repair_orphan_nodes  # noqa: E402,F401

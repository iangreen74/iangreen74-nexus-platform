"""Surgeon #3 — reingest-tenant operator action.

Codifies the manual reingest workflow:
1. Verify tenant + project exist
2. Rate limit: 1 reingest per tenant per 10 min unless force=true
3. Call Forgewing POST /reingest/{tenant_id}
4. Record OperatorAction audit trail

The aria-platform /reingest endpoint reads active_project_id from the
Tenant node in Neptune. F2 (aria 15885e2) threads project_id through
the pipeline. We ensure active_project_id is set before calling.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus.config import MODE
from nexus.operator_actions import _graph_query, _record_operator_action

log = logging.getLogger(__name__)

RATE_LIMIT_MINUTES = 10


class RateLimitError(Exception):
    """Reingest attempted too soon after a previous one."""
    def __init__(self, minutes_remaining: float):
        self.minutes_remaining = minutes_remaining
        super().__init__(f"Rate limited: {minutes_remaining:.0f}m remaining")


@dataclass
class ReingestResult:
    audit_id: str
    ingest_run_id: str | None
    status: str
    project_id: str
    tenant_id: str


def reingest_tenant(
    tenant_id: str,
    project_id: str | None = None,
    force: bool = False,
    operator_id: str = "ian",
) -> ReingestResult:
    """Queue a reingest for a tenant.

    Raises ValueError if tenant/project not found.
    Raises RateLimitError if rate limited and not force.
    """
    # 1. Verify tenant exists
    tenant = _graph_query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "RETURN t.tenant_id AS tid, t.repo_url AS repo, "
        "t.active_project_id AS active_pid",
        {"tid": tenant_id},
    )
    if not tenant:
        raise ValueError(f"tenant {tenant_id} not found")
    tenant_row = tenant[0]

    # 2. Resolve project_id
    if not project_id:
        project_id = tenant_row.get("active_pid") or ""
    if not project_id:
        projects = _graph_query(
            "MATCH (p:Project {tenant_id: $tid, status: 'active'}) "
            "RETURN p.project_id AS pid LIMIT 1",
            {"tid": tenant_id},
        )
        project_id = projects[0].get("pid", "") if projects else ""
    if not project_id:
        raise ValueError(f"no active project for tenant {tenant_id}")

    # 3. Verify project exists
    proj = _graph_query(
        "MATCH (p:Project {project_id: $pid, tenant_id: $tid}) "
        "RETURN p.project_id AS pid",
        {"pid": project_id, "tid": tenant_id},
    )
    if not proj:
        raise ValueError(f"project {project_id} not found on {tenant_id}")

    # 4. Rate limit check
    if not force:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(minutes=RATE_LIMIT_MINUTES)).isoformat()
        recent = _graph_query(
            "MATCH (r:IngestRun {tenant_id: $tid}) "
            "WHERE r.started_at > $cutoff "
            "RETURN r.started_at AS started LIMIT 1",
            {"tid": tenant_id, "cutoff": cutoff},
        )
        if recent:
            started = recent[0].get("started", "")
            try:
                dt = datetime.fromisoformat(
                    str(started).replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                remaining = (RATE_LIMIT_MINUTES * 60 - elapsed) / 60
            except Exception:
                remaining = RATE_LIMIT_MINUTES
            raise RateLimitError(max(0.1, remaining))

    # 5. Ensure active_project_id is set on Tenant node
    _graph_query(
        "MATCH (t:Tenant {tenant_id: $tid}) "
        "SET t.active_project_id = $pid",
        {"tid": tenant_id, "pid": project_id},
    )

    # 6. Call Forgewing /reingest
    ingest_run_id = None
    status = "queued"
    try:
        from nexus.capabilities.forgewing_api import call_api
        resp = call_api("POST", f"/reingest/{tenant_id}")
        if isinstance(resp, dict):
            if resp.get("error"):
                status = f"downstream_error: {resp['error']}"
            else:
                status = resp.get("status", "queued")
                ingest_run_id = resp.get("ingest_run_id")
    except Exception as e:
        status = f"call_failed: {e}"
        log.warning("reingest call failed for %s: %s", tenant_id, e)

    # 7. Record audit
    audit_id = _record_operator_action(
        action_type="reingest_tenant",
        operator_id=operator_id,
        tenant_id=tenant_id,
        result={
            "project_id": project_id,
            "status": status,
            "ingest_run_id": ingest_run_id,
            "force": force,
        },
    )

    log.info("reingest_tenant: %s project=%s status=%s audit=%s",
             tenant_id[:12], project_id[:12], status, audit_id[:12])

    return ReingestResult(
        audit_id=audit_id,
        ingest_run_id=ingest_run_id,
        status=status,
        project_id=project_id,
        tenant_id=tenant_id,
    )

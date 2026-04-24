"""Surgeon operator routes — curated remediation endpoints.

Password-gated via X-Operator-Password header. Every action writes
an :OperatorAction audit node to Neptune.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from pydantic import BaseModel
from typing import Any

from nexus.operator_actions import create_default_project, repair_orphan_nodes
from nexus.operator_purge import purge_orphan_nodes
from nexus.operator_reingest import RateLimitError, reingest_tenant

router = APIRouter()

_OPERATOR_PASSWORD = os.environ.get(
    "OPERATOR_PASSWORD", "aria-platform-2026"
)


def _verify_operator(password: str | None) -> str:
    """Verify operator password. Returns operator_id or raises 403."""
    if not password or password != _OPERATOR_PASSWORD:
        raise HTTPException(403, detail="invalid operator credential")
    return "ian"


@router.post("/tenants/{tenant_id}/create-default-project")
async def endpoint_create_default_project(
    tenant_id: str,
    x_operator_password: str | None = Header(None),
):
    """Create a missing default Project for a tenant."""
    operator_id = _verify_operator(x_operator_password)
    try:
        result = create_default_project(tenant_id, operator_id)
        status = 200 if result.created else 409
        return {
            "project_id": result.project_id,
            "created": result.created,
            "audit_id": result.audit_id,
        }
    except ValueError as e:
        raise HTTPException(404, detail=str(e))


class RepairOrphanRequest(BaseModel):
    target_project_id: str
    labels_to_repair: list[str] | None = None
    dry_run: bool = True


@router.post("/tenants/{tenant_id}/repair-orphan-nodes")
async def endpoint_repair_orphan_nodes(
    tenant_id: str,
    body: RepairOrphanRequest,
    x_operator_password: str | None = Header(None),
) -> dict[str, Any]:
    """Relabel orphan nodes to target project. Dry-run by default."""
    operator_id = _verify_operator(x_operator_password)
    try:
        return repair_orphan_nodes(
            tenant_id=tenant_id,
            target_project_id=body.target_project_id,
            labels_to_repair=body.labels_to_repair,
            dry_run=body.dry_run,
            operator_id=operator_id,
        )
    except ValueError as e:
        raise HTTPException(404, detail=str(e))


class PurgeOrphanRequest(BaseModel):
    labels_to_purge: list[str]
    dry_run: bool = True


@router.post("/tenants/{tenant_id}/purge-orphan-nodes")
async def endpoint_purge_orphan_nodes(
    tenant_id: str,
    body: PurgeOrphanRequest,
    x_operator_password: str | None = Header(None),
) -> dict[str, Any]:
    """DETACH DELETE orphan nodes for explicit labels. Dry-run by default.

    labels_to_purge is REQUIRED and must be non-empty — no default-to-all.
    """
    operator_id = _verify_operator(x_operator_password)
    if not body.labels_to_purge:
        raise HTTPException(
            400, detail="labels_to_purge is required and must be non-empty"
        )
    try:
        return purge_orphan_nodes(
            tenant_id=tenant_id,
            labels_to_purge=body.labels_to_purge,
            dry_run=body.dry_run,
            operator_id=operator_id,
        )
    except ValueError as e:
        msg = str(e)
        if "required" in msg or "non-empty" in msg:
            raise HTTPException(400, detail=msg)
        raise HTTPException(404, detail=msg)


class ReingestRequest(BaseModel):
    project_id: str | None = None
    force: bool = False


@router.post("/tenants/{tenant_id}/reingest")
async def endpoint_reingest_tenant(
    tenant_id: str,
    body: ReingestRequest = ReingestRequest(),
    x_operator_password: str | None = Header(None),
) -> dict[str, Any]:
    """Queue a reingest for a tenant. Returns 202 on success."""
    operator_id = _verify_operator(x_operator_password)
    try:
        result = reingest_tenant(
            tenant_id=tenant_id,
            project_id=body.project_id,
            force=body.force,
            operator_id=operator_id,
        )
        return {
            "audit_id": result.audit_id,
            "ingest_run_id": result.ingest_run_id,
            "status": result.status,
            "project_id": result.project_id,
        }
    except RateLimitError as e:
        raise HTTPException(429, detail=str(e))
    except ValueError as e:
        raise HTTPException(404, detail=str(e))

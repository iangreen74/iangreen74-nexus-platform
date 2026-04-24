"""Surgeon operator routes — curated remediation endpoints.

Password-gated via X-Operator-Password header. Every action writes
an :OperatorAction audit node to Neptune.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Header, HTTPException

from nexus.operator_actions import create_default_project

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

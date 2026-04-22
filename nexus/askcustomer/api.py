"""AskCustomer HTTP endpoints.

GET  /api/askcustomer/pending?tenant_id=...&project_id=...
POST /api/askcustomer/{proposal_id}/resolve
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from nexus.askcustomer import service

router = APIRouter(prefix="/api/askcustomer", tags=["askcustomer"])


@router.get("/pending")
async def pending(
    tenant_id: str = Query(...),
    project_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    """List pending asks for a tenant."""
    return service.list_pending(tenant_id, project_id)


@router.post("/{proposal_id}/resolve")
async def resolve(
    proposal_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Answer a pending ask. Resumes SFN execution if applicable."""
    answer = body.get("answer")
    answered_by = body.get("answered_by", "")
    if not answer or not answered_by:
        raise HTTPException(status_code=400, detail="answer and answered_by required")
    try:
        return service.resolve_ask(
            proposal_id=proposal_id, answer=answer, answered_by=answered_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except service.AskCustomerNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e))

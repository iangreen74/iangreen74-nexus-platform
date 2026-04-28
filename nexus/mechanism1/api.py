"""Mechanism 1 HTTP endpoints.

GET  /api/classifier/pending     — list pending proposals
POST /api/classifier/{id}/accept — disposition Accept
POST /api/classifier/{id}/edit   — disposition Edit
POST /api/classifier/{id}/reject — disposition Reject
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from nexus.mechanism1 import disposition, proposals

router = APIRouter(prefix="/api/classifier", tags=["classifier"])


class AcceptRequest(BaseModel):
    dispositioned_by: str


class EditRequest(BaseModel):
    edits: dict[str, Any]
    dispositioned_by: str


class RejectRequest(BaseModel):
    reason: str | None = None
    dispositioned_by: str


@router.get("/pending")
async def pending(
    tenant_id: str = Query(...),
    project_id: str | None = Query(None),
) -> list[dict[str, Any]]:
    return proposals.list_pending(tenant_id, project_id)


@router.post("/{candidate_id}/accept")
async def accept(candidate_id: str, body: AcceptRequest):
    try:
        return disposition.dispose(
            candidate_id, "accepted",
            dispositioned_by=body.dispositioned_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{candidate_id}/edit")
async def edit(candidate_id: str, body: EditRequest):
    try:
        return disposition.dispose(
            candidate_id, "edited",
            edits=body.edits,
            dispositioned_by=body.dispositioned_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{candidate_id}/reject")
async def reject(candidate_id: str, body: RejectRequest):
    try:
        return disposition.dispose(
            candidate_id, "rejected",
            reason=body.reason,
            dispositioned_by=body.dispositioned_by,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

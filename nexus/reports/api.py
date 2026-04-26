"""FastAPI router for Phase 2 reports.

Operator-auth model: requests reaching this router are already
Cognito-authenticated by the vaultscalerlabs.com ALB front door
(authenticate-cognito on the default listener rule, see
infra/overwatch-v2/13-alb.yml). This matches the convention of
``dashboard_routes`` and other operator-only routers in
``nexus/server.py`` — no FastAPI middleware is added here.

Endpoints:
  GET  /api/reports                     — catalog (12 reports, 3 feasible)
  POST /api/reports/{report_id}/run     — execute, returns envelope
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nexus.reports.runner import (
    ReportNotFoundError, list_reports, run_report,
)


router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("")
def get_catalog() -> dict:
    """Return the 12-entry catalog with feasibility + deferred reasons."""
    items = list_reports()
    return {
        "count": len(items),
        "feasible_count": sum(1 for it in items if it["feasible_now"]),
        "deferred_count": sum(1 for it in items if not it["feasible_now"]),
        "reports": items,
    }


@router.post("/{report_id}/run")
async def run(report_id: str, request: Request) -> dict:
    """Execute a report. JSON body is the params dict; empty body OK
    for reports with no parameters."""
    try:
        body: Any = await request.json()
    except Exception:
        body = {}
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="request body must be a JSON object of params (or empty)",
        )
    try:
        return run_report(report_id, params=body)
    except ReportNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"report {report_id!r} not in catalog",
        )
    except ValueError as e:
        # Builder-level validation (e.g. missing tenant_id).
        raise HTTPException(status_code=400, detail=str(e))

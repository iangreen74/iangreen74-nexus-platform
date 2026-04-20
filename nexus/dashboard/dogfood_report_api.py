"""Dogfood Diagnostic Report API — download endpoint.

GET  /api/dogfood/diagnostic-report — returns Markdown report as attachment
POST /api/dogfood/diagnostic-report — same (supports dashboard button trigger)

Query params:
    hours     — look-back window (1-48, default 6)
    run_limit — max runs to analyze (1-100, default 30)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from nexus.intelligence.dogfood_diagnostic_report import generate_diagnostic_report

logger = logging.getLogger("nexus.dashboard.dogfood_report_api")

router = APIRouter(prefix="/api/dogfood", tags=["dogfood-diagnostic"])


def _build_response(hours: int, run_limit: int) -> PlainTextResponse:
    """Generate report and wrap in a downloadable PlainTextResponse."""
    report = generate_diagnostic_report(hours=hours, run_limit=run_limit)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"dogfood-diagnostic-{ts}.md"
    return PlainTextResponse(
        content=report,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/diagnostic-report")
async def get_diagnostic_report(
    hours: int = Query(6, ge=1, le=48),
    run_limit: int = Query(30, ge=1, le=100),
) -> PlainTextResponse:
    """Generate and download a dogfood diagnostic report."""
    return _build_response(hours, run_limit)


@router.post("/diagnostic-report")
async def post_diagnostic_report(
    hours: int = Query(6, ge=1, le=48),
    run_limit: int = Query(30, ge=1, le=100),
) -> PlainTextResponse:
    """Generate and download a dogfood diagnostic report (POST for button trigger)."""
    return _build_response(hours, run_limit)

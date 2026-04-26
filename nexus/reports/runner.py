"""Report runner — single dispatcher used by the API and tests.

Envelope shape (every report returns this):
  {report_id, name, generated_at, params, sections, deferred_reason}

``deferred_reason`` is non-null when the report is deferred (no
builder runs); it lists the structured enum reasons from
:mod:`nexus.reports.catalog`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from nexus.reports.catalog import ReportSpec, build_catalog
from nexus.reports.tool_ctx import ToolCtx, production_ctx


class ReportNotFoundError(KeyError):
    pass


def list_reports() -> list[dict]:
    """Catalog view used by ``GET /api/reports``."""
    return [
        {
            "report_id": s.report_id,
            "name": s.name,
            "tier": s.tier,
            "audience": s.audience,
            "description": s.description,
            "params_schema": s.params_schema,
            "feasible_now": s.feasible_now,
            "deferred_reasons": list(s.deferred_reasons),
            "required_tools": list(s.required_tools),
        }
        for s in build_catalog().values()
    ]


def _envelope(spec: ReportSpec, params: dict, sections: list[dict],
              deferred_reasons: Optional[list[str]] = None) -> dict:
    return {
        "report_id": spec.report_id,
        "name": spec.name,
        "tier": spec.tier,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": params,
        "sections": sections,
        "deferred_reasons": deferred_reasons or [],
    }


def run_report(report_id: str, params: Optional[dict] = None,
               tool_ctx: Optional[ToolCtx] = None) -> dict:
    """Dispatch to the report's builder. Deferred reports return an
    empty-sections envelope with ``deferred_reasons`` populated."""
    catalog = build_catalog()
    spec = catalog.get(report_id)
    if spec is None:
        raise ReportNotFoundError(report_id)

    params = params or {}

    if not spec.feasible_now:
        return _envelope(
            spec, params, sections=[],
            deferred_reasons=list(spec.deferred_reasons),
        )

    if tool_ctx is None:
        tool_ctx = production_ctx()
    sections = spec.builder(params=params, tool_ctx=tool_ctx)
    return _envelope(spec, params, sections=sections)

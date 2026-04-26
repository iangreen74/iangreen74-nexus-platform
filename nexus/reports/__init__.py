"""Phase 2 reports — pre-computed structured views over fleet state.

Public surface: :func:`run_report` (orchestration) and the FastAPI router
in :mod:`nexus.reports.api`. The catalog of 12 reports lives in
:mod:`nexus.reports.catalog`; only 3 are feasible against the current
substrate (see ``docs/REPORTS_PHASE_2_INVENTORY.md``).
"""
from nexus.reports.runner import run_report

__all__ = ["run_report"]

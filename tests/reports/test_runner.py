"""Runner orchestration: dispatch, deferred envelopes, errors."""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.reports.runner import (  # noqa: E402
    ReportNotFoundError, list_reports, run_report,
)
from nexus.reports.tool_ctx import ToolCtx  # noqa: E402


def test_list_reports_returns_twelve_entries():
    items = list_reports()
    assert len(items) == 12
    assert all("feasible_now" in it for it in items)


def test_list_reports_groups_three_feasible_nine_deferred():
    items = list_reports()
    feasible = [it for it in items if it["feasible_now"]]
    deferred = [it for it in items if not it["feasible_now"]]
    assert len(feasible) == 3
    assert len(deferred) == 9
    # Every deferred entry exposes at least one structured reason.
    for d in deferred:
        assert d["deferred_reasons"]


def test_run_unknown_report_raises():
    with pytest.raises(ReportNotFoundError):
        run_report("does-not-exist")


def test_run_deferred_report_returns_empty_envelope_with_reasons():
    """Deferred reports return an empty-sections envelope plus the
    structured deferred_reasons — no builder runs, no tools called."""
    result = run_report("critical_findings_24h")
    assert result["report_id"] == "critical_findings_24h"
    assert result["sections"] == []
    assert "requires_phase_0b_log_correlation" in result["deferred_reasons"]
    # Generated_at + params still populated.
    assert result["generated_at"]
    assert result["params"] == {}


def test_run_feasible_report_with_injected_tool_ctx():
    """Feasible reports use the injected ToolCtx; production_ctx() is
    not called when a tool_ctx is passed."""
    captured = {}

    def fake_pipeline(**kw):
        captured["pipeline"] = kw
        return {"codebuild_projects": [], "cloudformation_stacks": []}

    def fake_state(**kw):
        captured["state"] = kw
        return {
            "ecs_services": [], "alb_targets": [], "cfn_stacks": [],
            "captured_at": "2026-04-26T00:00:00+00:00",
        }

    def fake_convs(**kw):
        return {"total_events": 0, "events_by_log_group": {}}

    def fake_ontology(**kw):
        return {"counts": {"recent_tasks": 0}}

    ctx = ToolCtx(handlers={
        "read_customer_tenant_state": fake_state,
        "read_customer_pipeline": fake_pipeline,
        "read_aria_conversations": fake_convs,
        "read_customer_ontology": fake_ontology,
    })
    result = run_report(
        "tenant_profile",
        params={"tenant_id": "forge-1dba4143ca24ed1f"},
        tool_ctx=ctx,
    )
    assert result["report_id"] == "tenant_profile"
    assert result["deferred_reasons"] == []
    assert len(result["sections"]) >= 1
    assert captured["state"]["tenant_id"] == "forge-1dba4143ca24ed1f"
    assert captured["pipeline"]["tenant_id"] == "forge-1dba4143ca24ed1f"

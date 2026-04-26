"""Catalog shape + feasibility tally."""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

from nexus.reports.catalog import (  # noqa: E402
    ALL_DEFER_REASONS, build_catalog,
)


def test_catalog_has_twelve_reports():
    assert len(build_catalog()) == 12


def test_three_reports_are_feasible():
    feasible = [s for s in build_catalog().values() if s.feasible_now]
    assert len(feasible) == 3
    assert {s.report_id for s in feasible} == {
        "fleet_health", "pipeline_activity_24h", "tenant_profile",
    }


def test_nine_reports_are_deferred():
    deferred = [s for s in build_catalog().values() if not s.feasible_now]
    assert len(deferred) == 9


def test_every_deferred_report_has_at_least_one_reason():
    for spec in build_catalog().values():
        if not spec.feasible_now:
            assert spec.deferred_reasons, (
                f"deferred report {spec.report_id} has no deferred_reasons"
            )


def test_every_deferred_reason_is_a_known_enum():
    for spec in build_catalog().values():
        for r in spec.deferred_reasons:
            assert r in ALL_DEFER_REASONS, f"unknown reason {r}"


def test_feasible_reports_have_no_deferred_reasons():
    for spec in build_catalog().values():
        if spec.feasible_now:
            assert spec.deferred_reasons == ()


def test_feasible_reports_declare_required_tools():
    for spec in build_catalog().values():
        if spec.feasible_now:
            assert spec.required_tools, (
                f"feasible report {spec.report_id} must declare required_tools"
            )


def test_tenant_profile_requires_tenant_id_param():
    spec = build_catalog()["tenant_profile"]
    assert spec.params_schema["tenant_id"]["required"] is True


def test_no_feasible_report_lists_tools_not_in_substrate():
    """Feasible reports must only consume tools from the current 15-tool surface."""
    fifteen_tools = {
        "read_aws_resource", "read_cloudwatch_logs", "read_github",
        "query_pipeline_truth", "query_engineering_ontology",
        "read_overwatch_metrics", "list_aws_resources",
        "read_repo_file", "search_codebase", "read_git_diff", "list_repo_files",
        "read_customer_tenant_state", "read_customer_pipeline",
        "read_customer_ontology", "read_aria_conversations",
    }
    for spec in build_catalog().values():
        if spec.feasible_now:
            unknown = set(spec.required_tools) - fifteen_tools
            assert not unknown, (
                f"{spec.report_id} consumes unregistered tools: {unknown}"
            )

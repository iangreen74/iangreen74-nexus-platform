"""Register V2 read tools with the global tool registry.

Imported once at chat-backend startup (Day 6). Idempotent — Track F's
register() overwrites a same-name spec rather than duplicating.

Track Q added list_aws_resources (catalog enumeration) — 7 tools.
Phase 0a (Track C) added the four codebase-indexing tools — 11 tools.
Phase 1 added four cross-tenant read tools — 15 tools.
Phase 0b adds four log-correlation tools (read_cloudtrail, read_alb_logs,
read_cloudwatch_metrics, query_correlated_events) — 19 tools total.
"""
from __future__ import annotations


def register_all_read_tools() -> None:
    """Call every tool module's register_tool() function."""
    from nexus.overwatch_v2.tools.read_tools import (
        alb_logs, aws_resource, cloudtrail, cloudwatch_logs,
        cloudwatch_metrics, correlated_events, engineering_ontology,
        github, list_aws_resources, list_repo_files, overwatch_metrics,
        pipeline_truth, read_aria_conversations, read_customer_ontology,
        read_customer_pipeline, read_customer_tenant_state,
        read_git_diff, read_repo_file, search_codebase,
    )
    aws_resource.register_tool()
    cloudwatch_logs.register_tool()
    github.register_tool()
    pipeline_truth.register_tool()
    engineering_ontology.register_tool()
    overwatch_metrics.register_tool()
    list_aws_resources.register_tool()
    # --- Phase 0a: codebase indexing ---
    read_repo_file.register_tool()
    search_codebase.register_tool()
    read_git_diff.register_tool()
    list_repo_files.register_tool()
    # --- Phase 1: cross-tenant read primitive ---
    read_customer_tenant_state.register_tool()
    read_customer_pipeline.register_tool()
    read_customer_ontology.register_tool()
    read_aria_conversations.register_tool()
    # --- Phase 0b: log-correlation substrate ---
    cloudtrail.register_tool()
    alb_logs.register_tool()
    cloudwatch_metrics.register_tool()
    correlated_events.register_tool()


if __name__ == "__main__":
    register_all_read_tools()
    from nexus.overwatch_v2.tools.registry import list_tools
    for spec in list_tools(include_mutations=False):
        ts = spec.get("toolSpec") or {}
        print(f"  registered: {ts.get('name')}")

"""Register V2 tools (read + write) with the global tool registry.

Imported once at chat-backend startup (Day 6). Idempotent — Track F's
register() overwrites a same-name spec rather than duplicating.

Track Q added list_aws_resources (catalog enumeration) — 7 tools.
Phase 0a (Track C) added the four codebase-indexing tools (read_repo_file,
search_codebase, read_git_diff, list_repo_files) — 11 tools.
Phase 1 added four cross-tenant read tools (read_customer_tenant_state,
read_customer_pipeline, read_customer_ontology, read_aria_conversations)
— 15 tools total.
Phase 0b adds four cross-source-log tools (read_cloudtrail,
read_alb_logs, query_correlated_events, read_cloudwatch_metrics) —
19 tools total.
Echo Phase 1 adds the first mutation tool (comment_on_pr,
requires_approval=True) — 20 tools total. The function name
register_all_read_tools is kept for caller compatibility; rename to
register_all_tools is a separate cleanup.
"""
from __future__ import annotations


def register_all_read_tools() -> None:
    """Call every tool module's register_tool() function."""
    from nexus.overwatch_v2.tools.read_tools import (
        aws_resource, cloudwatch_logs, engineering_ontology,
        github, list_aws_resources, list_repo_files, overwatch_metrics,
        pipeline_truth, query_correlated_events, read_alb_logs,
        read_aria_conversations, read_cloudtrail, read_cloudwatch_metrics,
        read_customer_ontology, read_customer_pipeline,
        read_customer_tenant_state, read_git_diff, read_repo_file,
        search_codebase,
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
    # --- Phase 0b: cross-source log index ---
    read_cloudtrail.register_tool()
    read_alb_logs.register_tool()
    query_correlated_events.register_tool()
    read_cloudwatch_metrics.register_tool()
    # --- Echo Phase 1: first mutation tool (approval-gated) ---
    from nexus.overwatch_v2.tools.write_tools import comment_on_pr
    comment_on_pr.register_tool()


if __name__ == "__main__":
    register_all_read_tools()
    from nexus.overwatch_v2.tools.registry import list_tools
    for spec in list_tools(include_mutations=True):
        ts = spec.get("toolSpec") or {}
        print(f"  registered: {ts.get('name')}")

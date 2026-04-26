"""V2 read tools registered with the Track F tool registry.

All tools are read-only; none require approval tokens. Each module exports:
  - PARAMETER_SCHEMA: JSON-schema-lite per the registry's parameter_validate
  - handler(params: dict) -> dict: pure function over the AWS / GitHub / internal API
  - register_tool() -> None: lazy-imports the registry and registers the spec

Lazy imports throughout — these modules are import-safe even if the
registry hasn't merged to main yet (see docs/V2_CONSTRUCTION_METHODOLOGY.md
L10).

Tool count: 11 (Day 6 + Track Q + Phase 0a):
  AWS / internal:  read_aws_resource, read_cloudwatch_logs,
                   read_overwatch_metrics, list_aws_resources,
                   query_pipeline_truth, query_engineering_ontology
  GitHub (V1):     read_github
  Codebase index:  read_repo_file, search_codebase, read_git_diff,
                   list_repo_files
"""

"""Echo synthesis layer (Phase 0b.5).

Internal helpers that normalize multi-source observability events into a
single shape Echo and the upcoming query_unified_events tool can reason
over. Existing 0b read tools at nexus/overwatch_v2/tools/read_tools/
remain frozen.

Phase 0b.5 sub-prompts:
    0b.5.1 — UnifiedEvent schema + per-source mappers (this module)
    0b.5.2 — filter DSL parser/compiler
    0b.5.3 — correlation-key bucketing
    0b.5.4 — Athena tables for long-range queries
    0b.5.5 — query_unified_events tool registration

Refs: /tmp/phase_0b5_design_20260426_1803.md.
"""

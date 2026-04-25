"""Six V2 read tools registered with the Track F tool registry.

All tools are read-only; none require approval tokens. Each module exports:
  - PARAMETER_SCHEMA: JSON-schema-lite per the registry's parameter_validate
  - handler(params: dict) -> dict: pure function over the AWS/internal API
  - register_tool() -> None: lazy-imports the registry and registers the spec

Lazy imports throughout — these modules are import-safe even if the
registry hasn't merged to main yet (see docs/V2_CONSTRUCTION_METHODOLOGY.md
L10).
"""

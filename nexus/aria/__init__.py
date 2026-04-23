"""ARIA prompt assembly — the central pipeline every ARIA response flows through.

Public API:
    assemble_aria_prompt(tenant_id, project_id, active_pills, turn_history)

Phase 4 of v6.1 implementation plan.
"""

from nexus.aria.prompt_assembly import assemble_aria_prompt

__all__ = ["assemble_aria_prompt"]

"""Persona loader — reads nexus/aria/persona.md as the ARIA voice definition.

Separate module from prompt_assembly so the persona source can be iterated
independently. Changing persona.md requires no code change — just prose.
"""
from __future__ import annotations

from pathlib import Path

_PERSONA_PATH = Path(__file__).parent / "persona.md"


def load_persona() -> str:
    """Read the persona definition from nexus/aria/persona.md.

    Returns the full markdown content minus HTML comment lines. Raises
    FileNotFoundError if persona.md is missing — ARIA should never
    operate without a persona defined.
    """
    if not _PERSONA_PATH.exists():
        raise FileNotFoundError(
            f"ARIA persona file missing: {_PERSONA_PATH}. "
            "See docs/design/ARIA_PERSONA_v1.md for the current draft."
        )
    raw = _PERSONA_PATH.read_text(encoding="utf-8")
    lines = [
        line for line in raw.splitlines()
        if not (line.strip().startswith("<!--") and line.strip().endswith("-->"))
    ]
    result = "\n".join(lines).strip()
    if not result:
        raise ValueError(
            f"Persona file {_PERSONA_PATH} contains only comments or is empty."
        )
    return result


def persona_token_estimate() -> int:
    """Rough token count using 4 chars/token heuristic.

    Not exact — use tiktoken if precision matters. For budget enforcement
    this is good enough since we add safety margin downstream.
    """
    return len(load_persona()) // 4

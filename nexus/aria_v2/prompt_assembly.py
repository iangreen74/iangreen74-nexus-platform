"""Echo's six-source prompt assembly. Mirrors nexus/aria/prompt_assembly.py.

Assembly never raises: any source that fails (Postgres, registry, ontology)
contributes an empty section. Persona (priority 0) is never trimmed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger("nexus.aria_v2.prompt_assembly")

MAX_TOTAL_TOKENS = 10_000
PERSONA_BUDGET = 2_000
CHARS_PER_TOKEN = 4

PERSONA_PATH = Path(__file__).parent / "persona.md"
OPERATOR_NAME = "Ian"


def _load_persona() -> str:
    try:
        body = PERSONA_PATH.read_text(encoding="utf-8")
    except Exception:
        log.exception("persona load failed")
        return "You are Echo, an engineering counterpart for the operator."
    marker = "## The draft prompt"
    idx = body.find(marker)
    if idx >= 0:
        return body[idx:].strip()
    return body.strip()


def _operator_section() -> tuple[str, str, int]:
    text = (
        "# Who you are talking to\n\n"
        f"You are talking to {OPERATOR_NAME}, the operator-engineer who "
        "designed and maintains this V2 Overwatch system. He is mid-task "
        "almost every time he opens a conversation. Meet him there.\n"
    )
    return ("operator", text, 10)


def _persona_section() -> tuple[str, str, int]:
    return ("persona", "# Who you are\n\n" + _load_persona() + "\n", 0)


def _ontology_section() -> tuple[str, str, int]:
    """Active investigations / hypotheses / patterns (Track E)."""
    try:
        from nexus.overwatch_v2.ontology import list_objects_by_type
    except Exception:
        return ("ontology", "", 30)
    try:
        invest = list_objects_by_type("Investigation", limit=10) or []
        hyps = list_objects_by_type("Hypothesis", limit=10) or []
        patts = list_objects_by_type("Pattern", limit=10) or []
    except Exception:
        log.exception("ontology read failed")
        return ("ontology", "", 30)
    if not (invest or hyps or patts):
        return ("ontology", "", 30)
    lines = ["# Active engineering memory"]
    if invest:
        lines.append("\n## Recent investigations")
        for o in invest[:10]:
            lines.append(f"- {o.get('hypothesis', '?')[:120]} "
                         f"(verdict={o.get('verdict','?')}, conf={o.get('confidence','?')})")
    if hyps:
        lines.append("\n## Recent hypotheses")
        for o in hyps[:10]:
            lines.append(f"- {o.get('claim', '?')[:120]} "
                         f"(status={o.get('status','?')})")
    if patts:
        lines.append("\n## Recurring patterns")
        for o in patts[:10]:
            lines.append(f"- {o.get('name', '?')}: {o.get('fix','')[:80]}")
    return ("ontology", "\n".join(lines) + "\n", 30)


def _sprint_section() -> tuple[str, str, int]:
    """Sprint context placeholder. Fills in once a canonical sprint doc exists."""
    text = (
        "# Sprint context\n\n"
        "Sprint 14 is in flight. V2 Overwatch construction is the focus. "
        "Echo (this assistant) is part of that work.\n"
    )
    return ("sprint", text, 40)


def _tools_section() -> tuple[str, str, int]:
    """Tool schemas as a human-readable summary; the actual tools[] array
    is passed alongside via Bedrock Converse, not in the system prompt."""
    try:
        from nexus.overwatch_v2.tools.registry import list_tools
        specs = list_tools(include_mutations=False) or []
    except Exception:
        return ("tools", "", 50)
    if not specs:
        return ("tools", "", 50)
    lines = ["# Tools you can call (read-only)"]
    for s in specs:
        ts = s.get("toolSpec") or {}
        name = ts.get("name", "?")
        desc = (ts.get("description") or "").strip()
        lines.append(f"- {name}: {desc[:200]}")
    return ("tools", "\n".join(lines) + "\n", 50)


def _history_section(history: Sequence[dict]) -> tuple[str, str, int]:
    if not history:
        return ("history", "", 100)
    lines = ["# Recent conversation"]
    for turn in list(history)[-20:]:
        role = turn.get("role", "?")
        content = turn.get("content")
        if isinstance(content, dict):
            text = content.get("text") or str(content)
        else:
            text = str(content)
        lines.append(f"{role}: {text[:1500]}")
    return ("history", "\n".join(lines) + "\n", 100)


def assemble_echo_prompt(conversation_id: str | None) -> str:
    """Return the complete system prompt for one Echo turn.

    Never raises — degraded sections become empty. Only persona is required.
    """
    history: list[dict] = []
    if conversation_id:
        try:
            from nexus.aria_v2 import persistence
            history = persistence.list_turns(conversation_id, limit=20)
        except Exception:
            log.exception("history load failed")
    sections = [
        _persona_section(),
        _operator_section(),
        _ontology_section(),
        _sprint_section(),
        _tools_section(),
        _history_section(history),
    ]
    return _compose_with_budget(sections)


def _compose_with_budget(
    sections: list[tuple[str, str, int]],
) -> str:
    """Compose; trim higher-priority sections first when over budget. Priority 0 never trims."""
    total_chars = sum(len(t) for _, t, _ in sections)
    budget_chars = MAX_TOTAL_TOKENS * CHARS_PER_TOKEN
    if total_chars <= budget_chars:
        return "\n\n".join(t for _, t, _ in sections if t.strip())
    ordered = sorted(sections, key=lambda s: s[2], reverse=True)
    while total_chars > budget_chars and ordered:
        name, text, priority = ordered[0]
        if priority == 0:
            break
        halved = text[: len(text) // 2] + "\n[... trimmed for budget ...]\n"
        total_chars -= len(text) - len(halved)
        ordered[0] = (name, halved, priority)
        if total_chars <= budget_chars:
            break
        if len(halved) < 100:
            total_chars -= len(halved)
            ordered.pop(0)
    original_order = {name: i for i, (name, _, _) in enumerate(sections)}
    final = sorted(ordered, key=lambda s: original_order[s[0]])
    return "\n\n".join(t for _, t, _ in final if t.strip())

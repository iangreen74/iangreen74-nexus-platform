"""Prompt assembly — the central ARIA pipeline.

Every turn flows through assemble_aria_prompt(). Combines persona, founder
context, ontology, tone, summaries, Socratic prompts, and history.
Token budget: 10k total, 2k persona never-trimmed, 8k context trimmed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from nexus.aria.ontology_reader import (
    FounderContext,
    OntologySubgraph,
    read_active_ontology,
    read_founder_context,
    read_recent_tone_markers,
    read_rolling_summaries,
)
from nexus.aria.persona import load_persona
from nexus.aria.socratic_reader import build_socratic_section, read_pending_socratic_prompts

MAX_TOTAL_TOKENS = 10_000
PERSONA_BUDGET = 2_000
CONTEXT_BUDGET = 8_000
CHARS_PER_TOKEN = 4


@dataclass
class ConversationTurn:
    """Single turn in the conversation history."""
    role: str  # "user" or "assistant"
    content: str
    timestamp: str | None = None


def assemble_aria_prompt(
    tenant_id: str,
    project_id: str | None,
    active_pills: Sequence[str],
    turn_history: Sequence[ConversationTurn],
) -> str:
    """Assemble ARIA's complete system prompt for this turn."""
    persona = load_persona()
    founder = read_founder_context(tenant_id)
    ontology = read_active_ontology(tenant_id, project_id, active_pills)
    tone_markers = read_recent_tone_markers(tenant_id)
    summaries = read_rolling_summaries(tenant_id)

    socratic = read_pending_socratic_prompts(tenant_id)
    sections = [
        _section_persona(persona),
        _section_founder(founder),
        _section_rolling_memory(summaries),
        _section_tone_context(tone_markers),
        _section_active_ontology(ontology, active_pills),
        build_socratic_section(socratic),
        _section_conversation_history(turn_history),
    ]
    return _compose_with_budget(sections)


# Each _section_* returns (name, text, trim_priority).
# Lower priority = trimmed last. Priority 0 = never trimmed.


def _section_persona(persona: str) -> tuple[str, str, int]:
    return ("persona", f"# Who you are\n\n{persona}\n", 0)


def _section_founder(founder: FounderContext) -> tuple[str, str, int]:
    has_data = any([
        founder.founder_name, founder.company_name, founder.stated_vision,
    ])
    if not has_data:
        return (
            "founder",
            "# Who you're talking to\n\n"
            "This is a new founder — you haven't yet learned their name, "
            "company, or vision. Your first job is to listen and learn. "
            "Ask what they're building. Ask what they've tried. Ask what "
            "they're worried about. Let them feel known before you do "
            "things.\n",
            10,
        )
    parts = ["# Who you're talking to\n"]
    if founder.founder_name:
        parts.append(f"Name: {founder.founder_name}")
    if founder.company_name:
        parts.append(f"Company: {founder.company_name}")
    if founder.stated_vision:
        parts.append(f"What they're building: {founder.stated_vision}")
    if founder.stage:
        parts.append(f"Stage: {founder.stage}")
    return ("founder", "\n".join(parts) + "\n", 20)


def _section_rolling_memory(summaries: dict) -> tuple[str, str, int]:
    parts = []
    if summaries.get("monthly"):
        parts.append(f"## This month\n{summaries['monthly']}")
    if summaries.get("weekly"):
        parts.append(f"## This week\n{summaries['weekly']}")
    if summaries.get("daily"):
        parts.append(f"## Today so far\n{summaries['daily']}")
    if not parts:
        return ("memory", "", 40)
    return (
        "memory",
        "# What you remember\n\n" + "\n\n".join(parts) + "\n",
        40,
    )


def _section_tone_context(
    tone_markers: list,
) -> tuple[str, str, int]:
    if not tone_markers:
        return ("tone", "", 50)
    summary = ", ".join(m.get("tone", "?") for m in tone_markers[:5])
    return (
        "tone",
        f"# Recent emotional weather\nLast turns: {summary}. "
        f"Respond to the arc, not only the words.\n",
        50,
    )


def _section_active_ontology(
    ontology: OntologySubgraph,
    active_pills: Sequence[str],
) -> tuple[str, str, int]:
    has_data = any([
        ontology.features, ontology.decisions,
        ontology.hypotheses, ontology.bugs,
    ])
    if not has_data:
        return ("ontology", "", 30)
    parts = []
    if active_pills:
        parts.append(f"Scoped to: {', '.join(active_pills)}")
    for label, items in [
        ("Active features", ontology.features),
        ("Open decisions", ontology.decisions),
        ("Running hypotheses", ontology.hypotheses),
        ("Known bugs", ontology.bugs),
    ]:
        if items:
            parts.append(f"## {label}")
            for obj in items[:10]:
                parts.append(f"- {obj.title} ({obj.status or '?'})")
    return (
        "ontology",
        "# What you know about the product\n\n" + "\n".join(parts) + "\n",
        30,
    )


def _section_conversation_history(
    history: Sequence[ConversationTurn],
) -> tuple[str, str, int]:
    if not history:
        return ("history", "", 100)
    parts = ["# Recent conversation"]
    for turn in history[-20:]:
        parts.append(f"{turn.role}: {turn.content}")
    return ("history", "\n".join(parts) + "\n", 100)


def _compose_with_budget(
    sections: list[tuple[str, str, int]],
) -> str:
    """Compose sections, trimming in priority order if over budget.

    Lower priority numbers are trimmed last. Priority 0 = never trimmed.
    """
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

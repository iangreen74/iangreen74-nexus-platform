"""Ontology reader — pulls structured context from Neptune for ARIA prompts.

Given a tenant_id + project_id + list of active pill scopes, returns
structured data that prompt_assembly.py formats into the system prompt.

This module does NOT format text. That's prompt_assembly's job.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class FounderContext:
    """What ARIA knows about this founder."""
    tenant_id: str
    founder_name: str | None = None
    company_name: str | None = None
    stated_vision: str | None = None
    stage: str | None = None  # e.g. "early-idea", "building-mvp"


@dataclass
class OntologyObject:
    """A node from the active ontology subgraph."""
    object_type: str  # Feature / Decision / Hypothesis / Bug
    title: str
    status: str | None = None
    created_at: str | None = None
    confidence: float | None = None
    related_ids: list[str] = field(default_factory=list)


@dataclass
class OntologySubgraph:
    """The filtered ontology context for the current conversation."""
    features: list[OntologyObject] = field(default_factory=list)
    decisions: list[OntologyObject] = field(default_factory=list)
    hypotheses: list[OntologyObject] = field(default_factory=list)
    bugs: list[OntologyObject] = field(default_factory=list)


def read_founder_context(tenant_id: str) -> FounderContext:
    """Read the UserContext node for this tenant.

    Returns FounderContext with name/company/vision/stage. If no UserContext
    exists (new tenant), returns context with all None fields — ARIA
    gracefully handles missing data.

    STUB: Phase 4b wires to Neptune. Returns empty context for now.
    """
    return FounderContext(tenant_id=tenant_id)


def read_active_ontology(
    tenant_id: str,
    project_id: str | None,
    active_pills: Iterable[str],
) -> OntologySubgraph:
    """Read the ontology subgraph scoped to the active pills.

    A pill is a scoping hint: "the plan", "PR #42", "the auth feature".
    Each pill maps to ontology node IDs. Given the pill set, return the
    subgraph of connected Features, Decisions, Hypotheses, Bugs — up to
    a size cap to prevent context explosion.

    If active_pills is empty, returns the top-N most recent ontology
    objects for this tenant+project as default context.

    STUB: Phase 4b wires to Neptune. Returns empty subgraph for now.
    """
    return OntologySubgraph()


def read_recent_tone_markers(
    tenant_id: str, limit: int = 5,
) -> list[dict[str, Any]]:
    """Read the last N tone markers from Postgres tone_markers table.

    Each marker: {tenant_id, turn_id, tone, urgency, seeking, mentions,
    confidence, created_at}. Reverse chronological (most recent first).

    Returns empty list on any error — prompt_assembly handles gracefully.
    """
    try:
        from nexus.mechanism1.tone_store import read_markers
        return read_markers(tenant_id, limit=limit)
    except Exception:
        return []


def read_rolling_summaries(tenant_id: str) -> dict[str, str | None]:
    """Read the current daily/weekly/monthly summaries for this founder.

    Returns dict with keys 'daily', 'weekly', 'monthly' — each either
    a string summary or None if not yet generated.

    STUB: Phase 6 populates.
    """
    return {"daily": None, "weekly": None, "monthly": None}

"""Ontology reader — pulls structured context from Neptune for ARIA prompts.

Given a tenant_id + project_id + list of active pill scopes, returns
structured data that prompt_assembly.py formats into the system prompt.

This module does NOT format text. That's prompt_assembly's job.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from nexus.aria.socratic_reader import read_pending_socratic_prompts  # noqa: F401

log = logging.getLogger(__name__)


@dataclass
class FounderContext:
    """What ARIA knows about this founder."""
    tenant_id: str
    founder_name: str | None = None
    company_name: str | None = None
    stated_vision: str | None = None
    stage: str | None = None


@dataclass
class OntologyObject:
    """A node from the active ontology subgraph."""
    object_type: str
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


def _graph_query(cypher: str, params: dict | None = None) -> list[dict]:
    """Query Neptune via overwatch_graph. Returns [] on any error."""
    try:
        from nexus.overwatch_graph import query
        return query(cypher, params or {}) or []
    except Exception as e:
        log.warning("graph query failed: %s", e)
        return []


def read_founder_context(tenant_id: str) -> FounderContext:
    """Read the UserContext node for this tenant from Neptune.

    Returns FounderContext populated from graph properties. If no node
    exists or Neptune is unreachable, returns empty context — ARIA
    shows "listen and learn" guidance for unknown founders.
    """
    try:
        rows = _graph_query(
            "MATCH (u:UserContext {tenant_id: $tid}) "
            "RETURN u.product_name AS name, u.product_vision AS vision, "
            "u.target_users AS target, u.source AS source "
            "LIMIT 1",
            {"tid": tenant_id},
        )
        if not rows or not isinstance(rows[0], dict):
            return FounderContext(tenant_id=tenant_id)
        r = rows[0]
        return FounderContext(
            tenant_id=tenant_id,
            company_name=r.get("name"),
            stated_vision=r.get("vision"),
        )
    except Exception as e:
        log.warning("read_founder_context failed: %s", e)
        return FounderContext(tenant_id=tenant_id)


def _query_ontology_type(
    tenant_id: str,
    project_id: str | None,
    label: str,
    title_field: str,
    pills: list[str],
    limit: int = 10,
) -> list[OntologyObject]:
    """Query one ontology type, returning OntologyObject list."""
    where = "n.tenant_id = $tid"
    params: dict[str, Any] = {"tid": tenant_id, "lim": limit}
    if project_id:
        where += " AND n.project_id = $pid"
        params["pid"] = project_id
    if pills:
        # Simple pill matching: filter by title containing any pill term
        pill_clauses = []
        for i, pill in enumerate(pills[:5]):
            key = f"pill{i}"
            pill_clauses.append(f"toLower(n.{title_field}) CONTAINS toLower(${key})")
            params[key] = pill
        where += f" AND ({' OR '.join(pill_clauses)})"
    rows = _graph_query(
        f"MATCH (n:{label}) WHERE {where} "
        f"RETURN n.{title_field} AS title, n.status AS status, "
        f"n.created_at AS created_at, n.id AS id "
        f"ORDER BY n.created_at DESC LIMIT $lim",
        params,
    )
    return [
        OntologyObject(
            object_type=label,
            title=r.get("title") or "(untitled)",
            status=r.get("status"),
            created_at=r.get("created_at"),
        )
        for r in rows if isinstance(r, dict) and r.get("title")
    ]


def read_active_ontology(
    tenant_id: str,
    project_id: str | None,
    active_pills: Iterable[str],
) -> OntologySubgraph:
    """Read the ontology subgraph scoped to the active pills.

    Queries Feature/Decision/Hypothesis nodes for this tenant+project.
    If active_pills is non-empty, filters by title match. Otherwise
    returns top-N most recent per type.

    Returns empty subgraph on any error — prompt_assembly handles it.
    """
    try:
        pills = list(active_pills or [])
        return OntologySubgraph(
            features=_query_ontology_type(
                tenant_id, project_id, "Feature", "name", pills),
            decisions=_query_ontology_type(
                tenant_id, project_id, "Decision", "name", pills),
            hypotheses=_query_ontology_type(
                tenant_id, project_id, "Hypothesis", "statement", pills),
        )
    except Exception as e:
        log.warning("read_active_ontology failed: %s", e)
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

    Returns {daily: str|None, weekly: str|None, monthly: str|None}
    with the most recent summary per horizon. Falls back to all-None
    on any error — prompt_assembly handles it cleanly.
    """
    try:
        from nexus.summaries.store import read_summaries
        return read_summaries(tenant_id)
    except Exception as e:
        log.warning("read_rolling_summaries fell back to None: %s", e)
        return {"daily": None, "weekly": None, "monthly": None}

"""Tests for ontology_reader — Phase 4b Neptune wire."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.aria.ontology_reader import (
    FounderContext,
    OntologyObject,
    OntologySubgraph,
    read_active_ontology,
    read_founder_context,
)


# --- read_founder_context -----------------------------------------------------


def test_read_founder_context_local_returns_empty():
    """In local mode, graph returns [] → empty FounderContext."""
    ctx = read_founder_context("forge-test")
    assert isinstance(ctx, FounderContext)
    assert ctx.tenant_id == "forge-test"
    assert ctx.founder_name is None
    assert ctx.stated_vision is None


def test_read_founder_context_happy_path(monkeypatch):
    """With graph data, returns populated FounderContext."""
    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query",
        lambda q, p=None: [
            {"name": "Acme Corp", "vision": "Build the best CRM",
             "target": "SMBs", "source": "onboarding"},
        ],
    )
    ctx = read_founder_context("forge-abc")
    assert ctx.company_name == "Acme Corp"
    assert ctx.stated_vision == "Build the best CRM"


def test_read_founder_context_no_node(monkeypatch):
    """Empty graph result → empty FounderContext."""
    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query",
        lambda q, p=None: [],
    )
    ctx = read_founder_context("forge-new")
    assert ctx.tenant_id == "forge-new"
    assert ctx.company_name is None


def test_read_founder_context_graph_error(monkeypatch):
    """Graph exception → empty FounderContext (graceful fallback)."""
    def _raise(*a, **kw):
        raise RuntimeError("Neptune unreachable")
    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query", _raise,
    )
    ctx = read_founder_context("forge-err")
    assert ctx.tenant_id == "forge-err"
    assert ctx.company_name is None


# --- read_active_ontology -----------------------------------------------------


def test_read_active_ontology_local_returns_empty():
    """In local mode, graph returns [] → empty subgraph."""
    sg = read_active_ontology("forge-test", None, [])
    assert isinstance(sg, OntologySubgraph)
    assert sg.features == []
    assert sg.decisions == []
    assert sg.hypotheses == []


def test_read_active_ontology_happy_path(monkeypatch):
    """With graph data, returns populated subgraph."""
    def _mock_query(q, p=None):
        if "Feature" in q:
            return [{"title": "OAuth SSO", "status": "proposed",
                     "created_at": "2026-04-23", "id": "f1"}]
        if "Decision" in q:
            return [{"title": "Use Postgres", "status": "active",
                     "created_at": "2026-04-22", "id": "d1"}]
        if "Hypothesis" in q:
            return [{"title": "Users want SSO", "status": "unvalidated",
                     "created_at": "2026-04-21", "id": "h1"}]
        return []

    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query", _mock_query,
    )
    sg = read_active_ontology("forge-abc", "proj-1", [])
    assert len(sg.features) == 1
    assert sg.features[0].title == "OAuth SSO"
    assert sg.features[0].object_type == "Feature"
    assert len(sg.decisions) == 1
    assert sg.decisions[0].title == "Use Postgres"
    assert len(sg.hypotheses) == 1


def test_read_active_ontology_with_pills(monkeypatch):
    """Pills filter by title matching."""
    captured = {}

    def _mock_query(q, p=None):
        captured["query"] = q
        captured["params"] = p
        return []

    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query", _mock_query,
    )
    read_active_ontology("forge-abc", None, ["auth", "SSO"])
    # Verify pill parameters were passed
    assert "pill0" in captured.get("params", {})
    assert captured["params"]["pill0"] == "auth"


def test_read_active_ontology_graph_error(monkeypatch):
    """Graph exception → empty subgraph (graceful fallback)."""
    def _raise(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query", _raise,
    )
    sg = read_active_ontology("forge-err", None, [])
    assert sg.features == []
    assert sg.decisions == []


def test_query_respects_limit(monkeypatch):
    """Query passes limit parameter."""
    captured = {}

    def _mock_query(q, p=None):
        captured["params"] = p
        return []

    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query", _mock_query,
    )
    read_active_ontology("forge-abc", None, [])
    assert captured["params"]["lim"] == 10


def test_ontology_object_skips_untitled(monkeypatch):
    """Rows with no title are filtered out."""
    monkeypatch.setattr(
        "nexus.aria.ontology_reader._graph_query",
        lambda q, p=None: [
            {"title": "", "status": "active"},
            {"title": "Real Feature", "status": "proposed"},
        ] if "Feature" in q else [],
    )
    sg = read_active_ontology("forge-abc", None, [])
    assert len(sg.features) == 1
    assert sg.features[0].title == "Real Feature"

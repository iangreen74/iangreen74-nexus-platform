"""Tests for ARIA prompt assembly — Phase 4 scaffold."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.aria.persona import load_persona, persona_token_estimate
from nexus.aria.ontology_reader import (
    FounderContext,
    OntologyObject,
    OntologySubgraph,
    read_active_ontology,
    read_founder_context,
    read_recent_tone_markers,
    read_rolling_summaries,
)
from nexus.aria.prompt_assembly import (
    CHARS_PER_TOKEN,
    MAX_TOTAL_TOKENS,
    ConversationTurn,
    assemble_aria_prompt,
)


# --- Persona tests -----------------------------------------------------------


def test_persona_loads_non_empty():
    text = load_persona()
    assert isinstance(text, str)
    assert len(text) > 100


def test_persona_token_estimate_reasonable():
    est = persona_token_estimate()
    assert isinstance(est, int)
    assert 50 <= est <= 2500


# --- Stub tests ---------------------------------------------------------------


def test_founder_context_stub_returns_empty_shape():
    ctx = read_founder_context("forge-test")
    assert isinstance(ctx, FounderContext)
    assert ctx.tenant_id == "forge-test"
    assert ctx.founder_name is None
    assert ctx.company_name is None
    assert ctx.stated_vision is None
    assert ctx.stage is None


def test_ontology_stub_returns_empty_subgraph():
    sg = read_active_ontology("forge-test", None, [])
    assert isinstance(sg, OntologySubgraph)
    assert sg.features == []
    assert sg.decisions == []
    assert sg.hypotheses == []
    assert sg.bugs == []


def test_tone_markers_stub_returns_empty_list():
    markers = read_recent_tone_markers("forge-test")
    assert markers == []


def test_summaries_stub_returns_none_keys():
    s = read_rolling_summaries("forge-test")
    assert s == {"daily": None, "weekly": None, "monthly": None}


# --- Assembly tests -----------------------------------------------------------


def test_assemble_empty_history_returns_string():
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=[],
    )
    assert isinstance(prompt, str)
    assert len(prompt) > 100


def test_assemble_includes_persona_text():
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=[],
    )
    assert "ARIA" in prompt
    assert "co-founder" in prompt


def test_assemble_new_founder_includes_listen_guidance():
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=[],
    )
    assert "listen" in prompt.lower()


def test_assemble_with_history_includes_turns():
    history = [
        ConversationTurn(role="user", content="I'm building a CRM"),
        ConversationTurn(role="assistant", content="Tell me more"),
        ConversationTurn(role="user", content="For small teams"),
    ]
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=history,
    )
    assert "I'm building a CRM" in prompt
    assert "Tell me more" in prompt
    assert "For small teams" in prompt


def test_assemble_with_active_pills_includes_scope(monkeypatch):
    ontology = OntologySubgraph(
        features=[OntologyObject(
            object_type="feature", title="Auth SSO", status="active",
        )],
    )
    monkeypatch.setattr(
        "nexus.aria.prompt_assembly.read_active_ontology",
        lambda *a, **kw: ontology,
    )
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id="p-1",
        active_pills=["the auth feature"], turn_history=[],
    )
    assert "Scoped to: the auth feature" in prompt
    assert "Auth SSO" in prompt


def test_assemble_with_founder_context(monkeypatch):
    ctx = FounderContext(
        tenant_id="forge-test",
        founder_name="Alice",
        company_name="Acme",
        stated_vision="Build the best CRM",
        stage="building-mvp",
    )
    monkeypatch.setattr(
        "nexus.aria.prompt_assembly.read_founder_context",
        lambda *a, **kw: ctx,
    )
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=[],
    )
    assert "Alice" in prompt
    assert "Acme" in prompt
    assert "Build the best CRM" in prompt


# --- Budget tests -------------------------------------------------------------


def test_assemble_budget_enforced():
    huge_history = [
        ConversationTurn(role="user", content="x" * 5000)
        for _ in range(20)
    ]
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=huge_history,
    )
    assert len(prompt) <= MAX_TOTAL_TOKENS * CHARS_PER_TOKEN


def test_assemble_persona_never_trimmed():
    huge_history = [
        ConversationTurn(role="user", content="y" * 5000)
        for _ in range(20)
    ]
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=huge_history,
    )
    persona = load_persona()
    # Persona should be fully present even when budget is exceeded
    assert persona[:200] in prompt


def test_assemble_history_trimmed_first():
    huge_history = [
        ConversationTurn(role="user", content="z" * 5000)
        for _ in range(20)
    ]
    prompt = assemble_aria_prompt(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=huge_history,
    )
    # History has trim_priority=100 (highest), so it's trimmed first
    assert "trimmed for budget" in prompt


# --- Determinism test ---------------------------------------------------------


def test_assemble_deterministic_for_same_inputs():
    args = dict(
        tenant_id="forge-test", project_id=None,
        active_pills=[], turn_history=[
            ConversationTurn(role="user", content="hello"),
        ],
    )
    p1 = assemble_aria_prompt(**args)
    p2 = assemble_aria_prompt(**args)
    assert p1 == p2

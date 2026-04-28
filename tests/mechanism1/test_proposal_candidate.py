"""Tests for ProposalCandidate Bug 4 rigorous-fix fields (PR-B).

Covers:
- Dataclass accepts the 7 new fields
- extract() populates Decision/Hypothesis fields per type
- Defaults applied: decided_by → 'founder', decided_at → current ISO
- INSERT path persists the new columns
- Per-type extraction is type-scoped (Decision fields None on Hypothesis row)
"""
from __future__ import annotations

import json
import os
import re
import uuid
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.mechanism1.classifier import (  # noqa: E402
    ProposalCandidate, _per_type_fields, extract,
)


# ---------------------------------------------------------------------------
# ProposalCandidate dataclass shape
# ---------------------------------------------------------------------------

def _base_candidate(**overrides) -> ProposalCandidate:
    defaults = {
        "candidate_id": str(uuid.uuid4()),
        "tenant_id": "t-x",
        "project_id": "p-x",
        "object_type": "decision",
        "title": "T",
        "summary": "S",
        "reasoning": "R",
        "confidence": 0.9,
        "source_turn_id": None,
    }
    defaults.update(overrides)
    return ProposalCandidate(**defaults)


def test_proposal_candidate_new_fields_default_none():
    c = _base_candidate()
    assert c.choice_made is None
    assert c.decided_at is None
    assert c.decided_by is None
    assert c.alternatives_considered is None
    assert c.statement is None
    assert c.why_believed is None
    assert c.how_will_be_tested is None


def test_proposal_candidate_accepts_new_fields():
    c = _base_candidate(
        choice_made="Postgres",
        decided_at="2026-04-15T12:00:00+00:00",
        decided_by="founder",
        alternatives_considered="DynamoDB, Firestore",
        statement="X correlates with Y",
        why_believed="prior data",
        how_will_be_tested="cohort comparison",
    )
    d = c.to_dict()
    assert d["choice_made"] == "Postgres"
    assert d["decided_by"] == "founder"
    assert d["alternatives_considered"] == "DynamoDB, Firestore"
    assert d["statement"] == "X correlates with Y"


# ---------------------------------------------------------------------------
# _per_type_fields helper
# ---------------------------------------------------------------------------

def test_per_type_fields_decision_full():
    fields = _per_type_fields("decision", {
        "choice_made": "Postgres",
        "decided_at": "2026-04-15",
        "decided_by": "CTO",
        "alternatives_considered": "DynamoDB",
    })
    assert fields == {
        "choice_made": "Postgres",
        "decided_at": "2026-04-15",
        "decided_by": "CTO",
        "alternatives_considered": "DynamoDB",
    }


def test_per_type_fields_decision_defaults():
    """Haiku omitting decided_at/decided_by gets default values."""
    fields = _per_type_fields("decision", {"choice_made": "React"})
    assert fields["choice_made"] == "React"
    assert fields["alternatives_considered"] is None
    assert fields["decided_by"] == "founder"
    # decided_at defaulted to current UTC ISO — verify shape, not value
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", fields["decided_at"])


def test_per_type_fields_hypothesis():
    fields = _per_type_fields("hypothesis", {
        "statement": "X causes Y",
        "why_believed": "observation",
        "how_will_be_tested": "A/B test",
    })
    assert fields == {
        "statement": "X causes Y",
        "why_believed": "observation",
        "how_will_be_tested": "A/B test",
    }


def test_per_type_fields_hypothesis_omitted_field():
    """Hypothesis with missing how_will_be_tested → None (prompt should suggest)."""
    fields = _per_type_fields("hypothesis", {"statement": "X", "why_believed": "Y"})
    assert fields["statement"] == "X"
    assert fields["how_will_be_tested"] is None


def test_per_type_fields_feature_empty():
    """Feature has no per-type Bug-4 fields — returns empty dict."""
    assert _per_type_fields("feature", {"choice_made": "ignored"}) == {}


# ---------------------------------------------------------------------------
# extract() integration — per-type field plumbing
# ---------------------------------------------------------------------------

def _haiku(proposal: dict | None) -> MagicMock:
    body_text = json.dumps({"proposal": proposal})
    body_stream = MagicMock()
    body_stream.read.return_value = json.dumps({
        "content": [{"type": "text", "text": body_text}],
    }).encode()
    return {"body": body_stream}


def test_extract_decision_populates_per_type_fields(monkeypatch):
    monkeypatch.setattr("nexus.config.MODE", "production")
    proposal = {
        "title": "Picked Postgres",
        "summary": "DB choice.",
        "reasoning": "alternatives weighed.",
        "confidence": 0.9,
        "choice_made": "Postgres",
        "alternatives_considered": "DynamoDB, Firestore",
        "decided_at": None,
        "decided_by": None,
    }
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _haiku(proposal)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(conversation_turn="…", tenant_id="t")

    decisions = [c for c in candidates if c.object_type == "decision"]
    assert decisions
    d = decisions[0]
    assert d.choice_made == "Postgres"
    assert d.alternatives_considered == "DynamoDB, Firestore"
    assert d.decided_by == "founder"  # defaulted
    assert d.decided_at is not None  # defaulted to ISO timestamp
    # Hypothesis fields stay None on a Decision row (per-type scoping)
    assert d.statement is None
    assert d.why_believed is None


def test_extract_hypothesis_populates_per_type_fields(monkeypatch):
    monkeypatch.setattr("nexus.config.MODE", "production")
    proposal = {
        "title": "Retention hypothesis",
        "summary": "Decision-capture predicts retention.",
        "reasoning": "testable claim.",
        "confidence": 0.85,
        "statement": "Founders using Decision-capture in W1 retain at 2x in M3.",
        "why_believed": "Early-engagement → sustained-engagement pattern.",
        "how_will_be_tested": "Cohort comparison of M3 retention.",
    }
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _haiku(proposal)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(conversation_turn="…", tenant_id="t")

    hyps = [c for c in candidates if c.object_type == "hypothesis"]
    assert hyps
    h = hyps[0]
    assert h.statement.startswith("Founders using Decision-capture")
    assert h.why_believed
    assert h.how_will_be_tested
    # Decision fields stay None on a Hypothesis row
    assert h.choice_made is None
    assert h.decided_at is None
    assert h.decided_by is None


def test_extract_feature_unaffected(monkeypatch):
    """Feature path doesn't pick up per-type fields — preserves PR-B Feature-as-baseline."""
    monkeypatch.setattr("nexus.config.MODE", "production")
    proposal = {
        "title": "One-click checkout", "summary": "U.", "reasoning": "feat.",
        "confidence": 0.9,
    }
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _haiku(proposal)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(conversation_turn="…", tenant_id="t")

    feats = [c for c in candidates if c.object_type == "feature"]
    assert feats
    f = feats[0]
    assert all(getattr(f, k) is None for k in (
        "choice_made", "decided_at", "decided_by", "alternatives_considered",
        "statement", "why_believed", "how_will_be_tested",
    ))


# ---------------------------------------------------------------------------
# enqueue_proposal — INSERT carries new columns
# ---------------------------------------------------------------------------

def test_enqueue_proposal_includes_new_columns(monkeypatch):
    """INSERT column list and params include all 7 Bug-4 columns."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._pg_connect", lambda: mock_conn,
    )

    from nexus.mechanism1.proposals import enqueue_proposal
    candidate = _base_candidate(
        object_type="decision",
        choice_made="Postgres",
        decided_at="2026-04-15T12:00:00+00:00",
        decided_by="founder",
        alternatives_considered="DynamoDB, Firestore",
    )
    enqueue_proposal(candidate)

    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    for col in ("choice_made", "decided_at", "decided_by",
                "alternatives_considered", "statement", "why_believed",
                "how_will_be_tested"):
        assert col in sql, f"INSERT missing column {col}"
    assert "Postgres" in params
    assert "DynamoDB, Firestore" in params
    assert "founder" in params


def test_enqueue_proposal_binds_null_for_unused_per_type_fields(monkeypatch):
    """A Decision row binds None for Hypothesis fields and vice versa."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._pg_connect", lambda: mock_conn,
    )

    from nexus.mechanism1.proposals import enqueue_proposal
    candidate = _base_candidate(
        object_type="decision",
        choice_made="Postgres",
        decided_by="founder",
        decided_at="2026-04-15T12:00:00+00:00",
    )
    enqueue_proposal(candidate)

    params = mock_cursor.execute.call_args[0][1]
    # Hypothesis-only fields bound as None for a Decision row
    assert params.count(None) >= 3  # statement, why_believed, how_will_be_tested

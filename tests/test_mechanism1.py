"""Tests for Mechanism 1: Inline classifier + proposal disposition."""
import json
import os
import uuid
from unittest.mock import MagicMock, patch

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.mechanism1.classifier import (  # noqa: E402
    MIN_CONFIDENCE,
    ProposalCandidate,
    SUPPORTED_TYPES,
    extract,
)


# ---------------------------------------------------------------------------
# classifier.extract() tests
# ---------------------------------------------------------------------------

def _mock_haiku_response(proposal: dict | None) -> MagicMock:
    """Build a mock Bedrock invoke_model response."""
    body_text = json.dumps({"proposal": proposal})
    body_stream = MagicMock()
    body_stream.read.return_value = json.dumps({
        "content": [{"type": "text", "text": body_text}],
    }).encode()
    resp = {"body": body_stream}
    return resp


def test_extract_produces_candidates(monkeypatch):
    """extract() with confident Haiku response yields candidates."""
    monkeypatch.setattr("nexus.config.MODE", "production")
    proposal = {
        "title": "One-click checkout flow",
        "summary": "Users can purchase with a single click.",
        "reasoning": "Describes a user-facing capability.",
        "confidence": 0.85,
    }
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _mock_haiku_response(proposal)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(
            conversation_turn="We built one-click checkout today.",
            tenant_id="t-123",
            project_id="p-456",
            source_turn_id="turn-1",
        )

    assert len(candidates) == 3  # same response for all 3 types
    for c in candidates:
        assert isinstance(c, ProposalCandidate)
        assert c.confidence == 0.85
        assert c.tenant_id == "t-123"
        assert c.project_id == "p-456"
    types = {c.object_type for c in candidates}
    assert types == set(SUPPORTED_TYPES)


def test_extract_filters_low_confidence(monkeypatch):
    """Proposals below MIN_CONFIDENCE are excluded."""
    monkeypatch.setattr("nexus.config.MODE", "production")
    proposal = {
        "title": "Maybe a feature",
        "summary": "Not sure.",
        "reasoning": "Weak signal.",
        "confidence": 0.4,
    }
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _mock_haiku_response(proposal)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(
            conversation_turn="Something vague.",
            tenant_id="t-123",
        )

    assert candidates == []


def test_extract_handles_null_proposal(monkeypatch):
    """Haiku returning {"proposal": null} is handled gracefully."""
    monkeypatch.setattr("nexus.config.MODE", "production")
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = _mock_haiku_response(None)

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(
            conversation_turn="Just chatting.",
            tenant_id="t-123",
        )

    assert candidates == []


def test_extract_handles_malformed_json(monkeypatch):
    """Haiku returning unparseable text doesn't crash."""
    monkeypatch.setattr("nexus.config.MODE", "production")
    body_stream = MagicMock()
    body_stream.read.return_value = json.dumps({
        "content": [{"type": "text", "text": "I cannot do that."}],
    }).encode()
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = {"body": body_stream}

    with patch("nexus.mechanism1.classifier._bedrock_client",
               return_value=mock_client):
        candidates = extract(
            conversation_turn="Something.",
            tenant_id="t-123",
        )

    assert candidates == []


def test_extract_skips_in_local_mode():
    """Local mode returns no candidates (no Bedrock)."""
    candidates = extract(
        conversation_turn="We built a dashboard.",
        tenant_id="t-123",
    )
    assert candidates == []


# ---------------------------------------------------------------------------
# proposals tests (mocked Postgres)
# ---------------------------------------------------------------------------

def _make_candidate(**overrides) -> ProposalCandidate:
    defaults = {
        "candidate_id": str(uuid.uuid4()),
        "tenant_id": "t-test",
        "project_id": "p-test",
        "object_type": "feature",
        "title": "Test Feature",
        "summary": "A test feature.",
        "reasoning": "Because testing.",
        "confidence": 0.9,
        "source_turn_id": "turn-1",
    }
    defaults.update(overrides)
    return ProposalCandidate(**defaults)


def test_enqueue_proposal(monkeypatch):
    """enqueue_proposal calls INSERT on Postgres."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._pg_connect",
        lambda: mock_conn,
    )

    from nexus.mechanism1.proposals import enqueue_proposal
    candidate = _make_candidate()
    result = enqueue_proposal(candidate)

    assert result == candidate.candidate_id
    mock_cursor.execute.assert_called_once()
    sql = mock_cursor.execute.call_args[0][0]
    assert "INSERT INTO classifier_proposals" in sql
    # source_kind is hardcoded at the INSERT site to 'conversation_classifier'
    # (migration 012). Asserts the column is named in the column list AND the
    # literal value appears in the VALUES clause.
    assert "source_kind" in sql
    assert "'conversation_classifier'" in sql


def test_dispose_accept_calls_ontology(monkeypatch):
    """dispose(accepted) writes ontology object + ActionEvent."""
    cid = str(uuid.uuid4())
    raw = {"candidate_id": cid, "object_type": "feature",
           "title": "Feat", "summary": "S"}

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._fetch_candidate",
        lambda _: {
            "candidate_id": cid, "tenant_id": "t-1",
            "project_id": "p-1", "object_type": "feature",
            "title": "Feat", "summary": "S", "reasoning": "R",
            "confidence": 0.9, "source_turn_id": "turn-1",
            "raw_candidate": raw,
        },
    )
    monkeypatch.setattr(
        "nexus.mechanism1.proposals._mark_disposed",
        lambda *a, **kw: None,
    )

    propose_mock = MagicMock(return_value={
        "object_id": "obj-1", "version_id": 1,
        "action_event_id": "ae-1", "pg_version_id": "pv-1",
    })
    eval_mock = MagicMock(return_value="ev-1")
    monkeypatch.setattr(
        "nexus.ontology.service.propose_object", propose_mock,
    )
    monkeypatch.setattr(
        "nexus.ontology.eval_corpus.write_action_event", eval_mock,
    )

    from nexus.mechanism1 import proposals
    result = proposals.dispose(cid, "accepted", dispositioned_by="ian")

    assert result["ontology_id"] == "obj-1"
    assert result["disposition"] == "accepted"
    propose_mock.assert_called_once()


def test_dispose_reject_no_ontology(monkeypatch):
    """dispose(rejected) writes ActionEvent but NO ontology object."""
    cid = str(uuid.uuid4())

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._fetch_candidate",
        lambda _: {
            "candidate_id": cid, "tenant_id": "t-1",
            "project_id": "p-1", "object_type": "decision",
            "title": "Dec", "summary": "S", "reasoning": "R",
            "confidence": 0.7, "source_turn_id": "turn-2",
            "raw_candidate": {},
        },
    )
    monkeypatch.setattr(
        "nexus.mechanism1.proposals._mark_disposed",
        lambda *a, **kw: None,
    )

    propose_mock = MagicMock()
    eval_mock = MagicMock(return_value="ev-1")
    monkeypatch.setattr(
        "nexus.ontology.service.propose_object", propose_mock,
    )
    monkeypatch.setattr(
        "nexus.ontology.eval_corpus.write_action_event", eval_mock,
    )

    from nexus.mechanism1 import proposals
    result = proposals.dispose(
        cid, "rejected", reason="Not relevant",
        dispositioned_by="ian",
    )

    assert result["ontology_id"] is None
    assert result["disposition"] == "rejected"
    propose_mock.assert_not_called()
    eval_mock.assert_called_once()


def test_dispose_edit_applies_edits(monkeypatch):
    """dispose(edited) passes edits to ontology propose_object."""
    cid = str(uuid.uuid4())

    monkeypatch.setattr(
        "nexus.mechanism1.proposals._fetch_candidate",
        lambda _: {
            "candidate_id": cid, "tenant_id": "t-1",
            "project_id": "p-1", "object_type": "hypothesis",
            "title": "Original", "summary": "Old",
            "reasoning": "R", "confidence": 0.8,
            "source_turn_id": "turn-3",
            "raw_candidate": {},
        },
    )
    monkeypatch.setattr(
        "nexus.mechanism1.proposals._mark_disposed",
        lambda *a, **kw: None,
    )

    propose_mock = MagicMock(return_value={
        "object_id": "obj-2", "version_id": 1,
        "action_event_id": "ae-2", "pg_version_id": "pv-2",
    })
    eval_mock = MagicMock(return_value="ev-2")
    monkeypatch.setattr(
        "nexus.ontology.service.propose_object", propose_mock,
    )
    monkeypatch.setattr(
        "nexus.ontology.eval_corpus.write_action_event", eval_mock,
    )

    from nexus.mechanism1 import proposals
    result = proposals.dispose(
        cid, "edited",
        edits={"title": "Edited Title", "summary": "New summary"},
        dispositioned_by="ian",
    )

    assert result["ontology_id"] == "obj-2"
    call_kwargs = propose_mock.call_args
    props = call_kwargs.kwargs.get("properties") or call_kwargs[1].get("properties")
    assert props["title"] == "Edited Title"
    assert props["summary"] == "New summary"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from nexus.server import app  # noqa: E402

client = TestClient(app)


def test_classifier_pending_endpoint():
    """GET /api/classifier/pending returns 200."""
    resp = client.get("/api/classifier/pending?tenant_id=t-test")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_classifier_accept_endpoint_no_db():
    """POST accept without DB raises ClassifierNotConfiguredError."""
    from nexus.mechanism1.proposals import ClassifierNotConfiguredError
    with pytest.raises(ClassifierNotConfiguredError):
        client.post(
            f"/api/classifier/{uuid.uuid4()}/accept",
            json={"dispositioned_by": "test"},
        )

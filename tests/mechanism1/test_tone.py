"""Tests for tone classifier and tone store — Phase 5."""
import json
import os
from unittest.mock import MagicMock

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.mechanism1.tone import (
    ToneMarker,
    _extract_json,
    classify_tone,
)
from nexus.mechanism1.tone_store import read_markers, save_marker


# --- JSON extraction ----------------------------------------------------------


def test_extract_json_plain():
    result = _extract_json('{"tone": "relaxed", "urgency": "low"}')
    assert result == {"tone": "relaxed", "urgency": "low"}


def test_extract_json_markdown_wrapped():
    text = '```json\n{"tone": "excited"}\n```'
    result = _extract_json(text)
    assert result["tone"] == "excited"


def test_extract_json_with_prose():
    text = 'Here is the classification: {"tone": "anxious", "urgency": "high"}'
    result = _extract_json(text)
    assert result["tone"] == "anxious"


def test_extract_json_malformed_returns_none():
    assert _extract_json("not json at all") is None


# --- classify_tone ------------------------------------------------------------


def test_classify_tone_empty_message_returns_none():
    assert classify_tone("", tenant_id="t-1", turn_id="turn-1") is None
    assert classify_tone("  ", tenant_id="t-1", turn_id="turn-1") is None


def test_classify_tone_skips_local_mode():
    """In local mode (no bedrock_client passed), returns None."""
    result = classify_tone(
        "I want to build a dashboard",
        tenant_id="t-1", turn_id="turn-1",
    )
    assert result is None


def _mock_bedrock(response_json):
    body_stream = MagicMock()
    body_stream.read.return_value = json.dumps({
        "content": [{"type": "text", "text": json.dumps(response_json)}],
    }).encode()
    client = MagicMock()
    client.invoke_model.return_value = {"body": body_stream}
    return client


def test_classify_tone_happy_path():
    client = _mock_bedrock({
        "tone": "excited", "urgency": "medium",
        "seeking": "execution", "mentions": ["Stripe"],
        "confidence": 0.92,
    })
    marker = classify_tone(
        "Let's integrate Stripe payments right now!",
        tenant_id="t-1", turn_id="turn-1",
        bedrock_client=client,
    )
    assert isinstance(marker, ToneMarker)
    assert marker.tone == "excited"
    assert marker.urgency == "medium"
    assert marker.seeking == "execution"
    assert marker.mentions == ["Stripe"]
    assert marker.confidence == 0.92


def test_classify_tone_bedrock_error_returns_none():
    client = MagicMock()
    client.invoke_model.side_effect = RuntimeError("bedrock down")
    result = classify_tone(
        "hello", tenant_id="t-1", turn_id="turn-1",
        bedrock_client=client,
    )
    assert result is None


def test_classify_tone_truncates_long_messages():
    client = _mock_bedrock({
        "tone": "relaxed", "urgency": "low",
        "seeking": "nothing_specific", "mentions": [],
        "confidence": 0.5,
    })
    long_msg = "x" * 10000
    marker = classify_tone(
        long_msg, tenant_id="t-1", turn_id="turn-1",
        bedrock_client=client,
    )
    assert marker is not None
    # Verify the prompt was truncated by checking the call
    call_body = json.loads(
        client.invoke_model.call_args.kwargs.get("body")
        or client.invoke_model.call_args[1]["body"]
    )
    prompt_text = call_body["messages"][0]["content"]
    # Message in prompt should be capped at 4000 chars
    assert len(prompt_text) < 5000


# --- ToneMarker ---------------------------------------------------------------


def test_marker_to_dict_shape():
    m = ToneMarker(
        tenant_id="t-1", turn_id="turn-1",
        tone="curious", urgency="low", seeking="clarity",
        mentions=["AWS"], confidence=0.8, timestamp="2026-04-23T12:00:00Z",
    )
    d = m.to_dict()
    assert d["tenant_id"] == "t-1"
    assert d["tone"] == "curious"
    assert d["urgency"] == "low"
    assert d["seeking"] == "clarity"
    assert d["mentions"] == ["AWS"]
    assert d["confidence"] == 0.8
    assert "turn_id" in d
    assert "timestamp" in d


# --- tone_store ---------------------------------------------------------------


def test_save_marker_success(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    monkeypatch.setattr(
        "nexus.mechanism1.tone_store._pg_connect", lambda: mock_conn,
    )
    marker = ToneMarker(
        tenant_id="t-1", turn_id="turn-1",
        tone="excited", urgency="high", seeking="execution",
    )
    result = save_marker(marker)
    assert result is True
    mock_cursor.execute.assert_called_once()


def test_save_marker_failure_returns_false(monkeypatch):
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.side_effect = RuntimeError("db down")

    monkeypatch.setattr(
        "nexus.mechanism1.tone_store._pg_connect", lambda: mock_conn,
    )
    marker = ToneMarker(
        tenant_id="t-1", turn_id="turn-1",
        tone="anxious", urgency="high", seeking="advice",
    )
    result = save_marker(marker)
    assert result is False


def test_read_markers_returns_empty_on_error(monkeypatch):
    from nexus.mechanism1.tone_store import ToneStoreNotConfiguredError
    monkeypatch.setattr(
        "nexus.mechanism1.tone_store._pg_connect",
        lambda: (_ for _ in ()).throw(ToneStoreNotConfiguredError("no db")),
    )
    result = read_markers("t-1")
    assert result == []


def test_read_markers_parses_jsonb_dict(monkeypatch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        ({"tone": "relaxed", "urgency": "low"}, now),
    ]
    monkeypatch.setattr(
        "nexus.mechanism1.tone_store._pg_connect", lambda: mock_conn,
    )
    result = read_markers("t-1")
    assert len(result) == 1
    assert result[0]["tone"] == "relaxed"
    assert result[0]["created_at"] is not None


def test_read_markers_parses_jsonb_string(monkeypatch):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cursor.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.fetchall.return_value = [
        ('{"tone": "confident", "seeking": "validation"}', now),
    ]
    monkeypatch.setattr(
        "nexus.mechanism1.tone_store._pg_connect", lambda: mock_conn,
    )
    result = read_markers("t-1")
    assert len(result) == 1
    assert result[0]["tone"] == "confident"

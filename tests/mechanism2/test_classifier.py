"""Tests for Mechanism 2 deploy event classifier."""
import json
import os
from io import BytesIO
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.mechanism2.classifier import (
    DeployProposal,
    _extract_json_array,
    classify_deploy_event,
)


# --- _extract_json_array ---------------------------------------------------


def test_extract_plain_array():
    assert _extract_json_array('[{"a":1}]') == [{"a": 1}]


def test_extract_markdown_wrapped():
    text = '```json\n[{"a":1}]\n```'
    assert _extract_json_array(text) == [{"a": 1}]


def test_extract_with_prose():
    text = 'Here are the results:\n[{"a":1}]\nDone.'
    assert _extract_json_array(text) == [{"a": 1}]


def test_extract_single_object_wraps():
    assert _extract_json_array('{"a":1}') == [{"a": 1}]


def test_extract_malformed_returns_empty():
    assert _extract_json_array("not json at all") == []


def test_extract_empty_array():
    assert _extract_json_array("[]") == []


# --- classify_deploy_event --------------------------------------------------


def _mock_bedrock_response(items: list) -> MagicMock:
    client = MagicMock()
    body = BytesIO(json.dumps({
        "content": [{"text": json.dumps(items)}]
    }).encode())
    client.invoke_model.return_value = {"body": body}
    return client


def test_classify_success_event():
    bedrock = _mock_bedrock_response([{
        "object_type": "Feature",
        "title": "Auth service proven",
        "summary": "Deploy succeeded — auth is shippable",
        "confidence": 0.85,
    }])
    event = {"tenant_id": "t-1", "project_id": "p-1",
             "event_type": "succeeded", "event_id": "ev-1"}
    result = classify_deploy_event(event, bedrock_client=bedrock)
    assert len(result) == 1
    assert result[0].object_type == "Feature"
    assert result[0].confidence == 0.85
    assert result[0].tenant_id == "t-1"


def test_classify_failure_event():
    bedrock = _mock_bedrock_response([{
        "object_type": "Bug",
        "title": "Container OOM on startup",
        "summary": "Deploy failed with exit code 137",
        "confidence": 0.9,
    }])
    event = {"tenant_id": "t-1", "event_type": "failed", "event_id": "ev-2"}
    result = classify_deploy_event(event, bedrock_client=bedrock)
    assert len(result) == 1
    assert result[0].object_type == "Bug"


def test_classify_empty_response():
    bedrock = _mock_bedrock_response([])
    event = {"tenant_id": "t-1", "event_type": "succeeded"}
    result = classify_deploy_event(event, bedrock_client=bedrock)
    assert result == []


def test_classify_missing_tenant_returns_empty():
    result = classify_deploy_event({"event_type": "succeeded"})
    assert result == []


def test_classify_bedrock_error_returns_empty():
    bedrock = MagicMock()
    bedrock.invoke_model.side_effect = Exception("timeout")
    result = classify_deploy_event(
        {"tenant_id": "t-1"}, bedrock_client=bedrock)
    assert result == []


def test_classify_filters_low_confidence():
    bedrock = _mock_bedrock_response([
        {"object_type": "Feature", "title": "A", "summary": "x",
         "confidence": 0.3},
        {"object_type": "Bug", "title": "B", "summary": "y",
         "confidence": 0.8},
    ])
    event = {"tenant_id": "t-1", "event_type": "failed"}
    result = classify_deploy_event(event, bedrock_client=bedrock)
    assert len(result) == 1
    assert result[0].object_type == "Bug"


def test_proposal_has_candidate_id():
    bedrock = _mock_bedrock_response([{
        "object_type": "Feature", "title": "A",
        "summary": "x", "confidence": 0.7,
    }])
    result = classify_deploy_event(
        {"tenant_id": "t-1", "event_id": "ev-1"},
        bedrock_client=bedrock)
    assert result[0].candidate_id  # non-empty UUID
    assert result[0].source_turn_id == "ev-1"


def test_proposal_to_dict():
    p = DeployProposal(
        candidate_id="c1", tenant_id="t1", project_id="p1",
        object_type="Feature", title="A", summary="B",
        reasoning="C", confidence=0.9, source_turn_id="e1")
    d = p.to_dict()
    assert d["candidate_id"] == "c1"
    assert d["source_kind"] == "deploy_event"

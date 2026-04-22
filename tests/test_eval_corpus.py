"""Tests for Layer 3 eval corpus writer."""
import json
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import patch, MagicMock
from nexus.ontology.eval_corpus import write_action_event

COMMON = dict(
    tenant_id="tenant-x", project_id="proj-y", ontology_id="ont-1",
    version_id="v-1", object_type="feature", mutation_kind="propose",
    caller="test", proposed_via="test", old_state=None,
    new_state={"name": "test feature"},
)


def test_write_calls_put_object():
    with patch("nexus.ontology.eval_corpus._s3_client") as mock_fn:
        mock_client = MagicMock()
        mock_fn.return_value = mock_client
        eid = write_action_event(**COMMON)
    assert eid is not None
    mock_client.put_object.assert_called_once()
    kw = mock_client.put_object.call_args.kwargs
    assert kw["Bucket"] == "forgewing-eval-corpus-418295677815"
    assert "tenant=tenant-x" in kw["Key"]
    assert kw["Key"].endswith(".jsonl")


def test_s3_unavailable_returns_none():
    with patch("nexus.ontology.eval_corpus._s3_client", return_value=None):
        assert write_action_event(**COMMON) is None


def test_put_object_failure_returns_none():
    with patch("nexus.ontology.eval_corpus._s3_client") as mock_fn:
        mock_client = MagicMock()
        mock_client.put_object.side_effect = Exception("S3 down")
        mock_fn.return_value = mock_client
        assert write_action_event(**COMMON) is None


def test_body_is_valid_json():
    with patch("nexus.ontology.eval_corpus._s3_client") as mock_fn:
        mock_client = MagicMock()
        mock_fn.return_value = mock_client
        write_action_event(**COMMON, metadata={"req": "r1"})
    body = mock_client.put_object.call_args.kwargs["Body"]
    event = json.loads(body)
    assert event["tenant_id"] == "tenant-x"
    assert event["mutation_kind"] == "propose"
    assert "event_id" in event
    assert "event_ts" in event
    assert event["metadata"] == {"req": "r1"}

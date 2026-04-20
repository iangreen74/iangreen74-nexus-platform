"""Tests for pipeline_event_sensor."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from nexus.sensors import pipeline_event_sensor as sensor

VALID_ENVELOPE = {
    "event_id": "evt-1", "event_type": "attempt_initialized",
    "event_version": "1.0", "emitted_at": "2026-04-20T12:00:00+00:00",
    "tenant_id": "forge-t", "project_id": "proj-p",
    "correlation_id": "att-1", "feature_id": None,
    "payload": {"stage": "initialized"},
}


def _sqs_msg(envelope, mid="m1"):
    return {
        "MessageId": mid, "ReceiptHandle": f"rh-{mid}",
        "Body": json.dumps({"source": "forgewing.deploy.v2", "detail": envelope}),
    }


@pytest.fixture
def mock_sqs(monkeypatch):
    client = MagicMock()
    client.receive_message.return_value = {"Messages": []}
    client.delete_message_batch.return_value = {}
    monkeypatch.setattr(sensor, "_sqs_client", lambda: client)
    monkeypatch.setenv("PIPELINE_EVENTS_QUEUE_URL", "https://sqs/q")
    return client


@pytest.fixture
def mock_record(monkeypatch):
    m = MagicMock(return_value="node-id")
    monkeypatch.setattr(sensor, "record_pipeline_event", m)
    return m


class TestConfig:
    def test_no_queue_url_skips(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_EVENTS_QUEUE_URL", raising=False)
        assert sensor.poll_pipeline_events()["skipped"] is True

    def test_empty_queue_url_skips(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_EVENTS_QUEUE_URL", "")
        assert sensor.poll_pipeline_events()["skipped"] is True


class TestHappyPath:
    def test_single_message(self, mock_sqs, mock_record):
        mock_sqs.receive_message.return_value = {"Messages": [_sqs_msg(VALID_ENVELOPE)]}
        r = sensor.poll_pipeline_events()
        assert r == {"polled": 1, "recorded": 1, "poison": 0, "errors": 0}
        mock_record.assert_called_once()
        kw = mock_record.call_args.kwargs
        assert kw["event_id"] == "evt-1"
        assert kw["correlation_id"] == "att-1"
        mock_sqs.delete_message_batch.assert_called_once()

    def test_batch(self, mock_sqs, mock_record):
        msgs = [_sqs_msg({**VALID_ENVELOPE, "event_id": f"e{i}"}, f"m{i}") for i in range(5)]
        mock_sqs.receive_message.return_value = {"Messages": msgs}
        r = sensor.poll_pipeline_events()
        assert r["recorded"] == 5
        assert mock_record.call_count == 5


class TestPoison:
    def test_bad_json(self, mock_sqs, mock_record):
        mock_sqs.receive_message.return_value = {"Messages": [
            {"MessageId": "m1", "ReceiptHandle": "rh", "Body": "not json {{"}
        ]}
        r = sensor.poll_pipeline_events()
        assert r["poison"] == 1 and r["recorded"] == 0
        mock_record.assert_not_called()

    def test_no_detail(self, mock_sqs, mock_record):
        mock_sqs.receive_message.return_value = {"Messages": [
            {"MessageId": "m1", "ReceiptHandle": "rh",
             "Body": json.dumps({"source": "x", "no_detail": True})}
        ]}
        assert sensor.poll_pipeline_events()["poison"] == 1

    def test_missing_required_field(self, mock_sqs, mock_record):
        bad = {k: v for k, v in VALID_ENVELOPE.items() if k != "tenant_id"}
        mock_sqs.receive_message.return_value = {"Messages": [_sqs_msg(bad)]}
        assert sensor.poll_pipeline_events()["poison"] == 1


class TestNeptuneFailure:
    def test_leaves_message_for_redelivery(self, mock_sqs, mock_record):
        mock_record.side_effect = RuntimeError("neptune down")
        mock_sqs.receive_message.return_value = {"Messages": [_sqs_msg(VALID_ENVELOPE)]}
        r = sensor.poll_pipeline_events()
        assert r["errors"] == 1 and r["recorded"] == 0
        if mock_sqs.delete_message_batch.called:
            entries = mock_sqs.delete_message_batch.call_args.kwargs.get("Entries", [])
            assert not any(e["Id"] == "m1" for e in entries)


class TestSqsFailure:
    def test_receive_error_no_crash(self, mock_sqs, mock_record):
        mock_sqs.receive_message.side_effect = RuntimeError("throttled")
        r = sensor.poll_pipeline_events()
        assert r["errors"] == 1 and r["polled"] == 0


class TestFeatureId:
    def test_passthrough(self, mock_sqs, mock_record):
        mock_sqs.receive_message.return_value = {"Messages": [
            _sqs_msg({**VALID_ENVELOPE, "feature_id": "feat-xyz"})
        ]}
        sensor.poll_pipeline_events()
        assert mock_record.call_args.kwargs["feature_id"] == "feat-xyz"

    def test_none_default(self, mock_sqs, mock_record):
        mock_sqs.receive_message.return_value = {"Messages": [_sqs_msg(VALID_ENVELOPE)]}
        sensor.poll_pipeline_events()
        assert mock_record.call_args.kwargs["feature_id"] is None


class TestEmptyQueue:
    def test_returns_zeros(self, mock_sqs, mock_record):
        r = sensor.poll_pipeline_events()
        assert r == {"polled": 0, "recorded": 0, "poison": 0, "errors": 0}
        mock_record.assert_not_called()

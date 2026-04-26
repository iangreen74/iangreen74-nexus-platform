"""Tests for Phase 0b read_cloudtrail tool."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from nexus.overwatch_v2.tools.read_tools import read_cloudtrail
from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown


def _fake_event(name="UpdateService", who="ian", ts=None):
    return {
        "EventName": name,
        "EventTime": ts or datetime.now(timezone.utc),
        "EventId": "evt-" + name.lower(),
        "Username": who,
        "Resources": [{"ResourceType": "AWS::ECS::Service",
                       "ResourceName": "arn:aws:ecs:us-east-1:418295677815:service/c/s"}],
        "CloudTrailEvent": '{"eventName":"' + name + '"}',
    }


def test_default_window_60min(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": [_fake_event()]}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler()
    assert r["source"] == "cloudtrail"
    s = datetime.fromisoformat(r["time_range"]["start"])
    e = datetime.fromisoformat(r["time_range"]["end"])
    assert 59 * 60 <= (e - s).total_seconds() <= 61 * 60
    assert r["count"] == 1
    assert r["events"][0]["event_name"] == "UpdateService"


def test_window_capped_at_24h(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": []}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler(
        start_time="2026-04-26T00:00:00Z",
        end_time="2026-04-30T00:00:00Z",  # 96h, way over cap
    )
    s = datetime.fromisoformat(r["time_range"]["start"])
    e = datetime.fromisoformat(r["time_range"]["end"])
    assert (e - s) == timedelta(hours=24)
    assert r["truncated"] is True


def test_event_name_filter_uses_lookup_attribute(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": []}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    read_cloudtrail.handler(event_name="AssumeRole")
    args = fake.lookup_events.call_args.kwargs
    assert args["LookupAttributes"] == [
        {"AttributeKey": "EventName", "AttributeValue": "AssumeRole"},
    ]


def test_resource_arn_filter_when_no_event_name(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": []}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    read_cloudtrail.handler(resource_arn="arn:aws:ecs:us-east-1:1:service/x/y")
    args = fake.lookup_events.call_args.kwargs
    assert args["LookupAttributes"][0]["AttributeKey"] == "ResourceName"


def test_event_name_takes_priority_over_arn(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": []}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    read_cloudtrail.handler(event_name="UpdateService", resource_arn="arn:foo")
    args = fake.lookup_events.call_args.kwargs
    assert args["LookupAttributes"][0]["AttributeKey"] == "EventName"


def test_max_events_capped_at_500(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {
        "Events": [_fake_event() for _ in range(50)],
    }
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler(max_events=99999)
    assert r["count"] == 50  # only 50 returned per call


def test_pagination_aggregates_events(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.side_effect = [
        {"Events": [_fake_event() for _ in range(50)], "NextToken": "tok"},
        {"Events": [_fake_event() for _ in range(50)]},
    ]
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler(max_events=200)
    assert r["count"] == 100


def test_pagination_marks_truncated_when_token_remains(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {
        "Events": [_fake_event() for _ in range(50)],
        "NextToken": "tok",
    }
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler(max_events=50)
    assert r["truncated"] is True


def test_bad_iso_timestamp_raises():
    with pytest.raises(ToolUnknown, match="ISO-8601"):
        read_cloudtrail.handler(start_time="not-a-time")


def test_event_shape_has_required_fields(monkeypatch):
    fake = MagicMock()
    fake.lookup_events.return_value = {"Events": [_fake_event(name="DescribeStacks")]}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_cloudtrail.handler()
    ev = r["events"][0]
    for key in ("timestamp", "event_id", "event_name", "principal", "resources", "raw"):
        assert key in ev

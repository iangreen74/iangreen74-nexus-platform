"""Phase 0b tool unit tests: cloudtrail, alb_logs, cloudwatch_metrics,
correlated_events. AWS calls mocked via patching ``nexus.aws_client._client``.
"""
from __future__ import annotations

import gzip
import os

os.environ.setdefault("NEXUS_MODE", "local")

from datetime import datetime, timedelta, timezone  # noqa: E402
from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.overwatch_v2.tools.read_tools import (  # noqa: E402
    _alb_log_parser, alb_logs, cloudtrail, cloudwatch_metrics,
    correlated_events,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (  # noqa: E402
    ToolUnknown,
)


# --- _alb_log_parser ------------------------------------------------------

def test_parse_log_line_returns_full_field_map():
    line = (
        'https 2026-04-25T22:01:00.000000Z app/overwatch-v2-alb/abc123 '
        '54.197.116.25:55000 10.100.10.239:9001 0.001 0.020 0.000 200 200 '
        '300 1500 "GET https://vaultscalerlabs.com:443/health HTTP/1.1" '
        '"curl/7.81.0" ECDHE-RSA-AES128-GCM-SHA256 TLSv1.3 '
        'arn:aws:elasticloadbalancing:us-east-1:418295677815:targetgroup/overwatch-v2-tg/abc '
        '"Root=1-abc" "vaultscalerlabs.com" "arn:aws:acm:..." 6 '
        '2026-04-25T22:00:59.999000Z "forward" "-" "-" "10.100.10.239:9001" '
        '"200" "-" "-"'
    )
    rec = _alb_log_parser.parse_log_line(line)
    assert rec is not None
    assert rec["type"] == "https"
    assert rec["elb_status_code"] == "200"
    assert rec["domain_name"] == "vaultscalerlabs.com"


def test_parse_log_line_returns_none_for_blank():
    assert _alb_log_parser.parse_log_line("") is None
    assert _alb_log_parser.parse_log_line("   ") is None


def test_matches_status_prefix_filter():
    rec = {"elb_status_code": "503"}
    assert _alb_log_parser.matches(rec, {"status_prefix": "5"})
    assert not _alb_log_parser.matches(rec, {"status_prefix": "2"})


def test_matches_empty_filter_passes():
    assert _alb_log_parser.matches({"elb_status_code": "200"}, {})


# --- read_cloudtrail ------------------------------------------------------

def _ct_event(name: str = "AssumeRole") -> dict:
    return {
        "EventId": "id-1", "EventName": name, "EventSource": "sts.amazonaws.com",
        "Username": "ian-myceliux",
        "EventTime": datetime(2026, 4, 26, 1, 0, 0, tzinfo=timezone.utc),
        "Resources": [{"ResourceType": "AWS::IAM::Role", "ResourceName": "x"}],
        "ReadOnly": "true", "AccessKeyId": "AKIA...",
    }


def test_cloudtrail_happy_path():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{
        "Events": [_ct_event(), _ct_event("DescribeStacks")],
    }]
    with patch("nexus.aws_client._client", return_value=fake):
        r = cloudtrail.handler(max_events=5)
    assert r["total_count"] == 2
    assert r["events"][0]["event_name"] == "AssumeRole"
    assert r["events"][1]["event_name"] == "DescribeStacks"


def test_cloudtrail_filter_attribute_validated():
    with pytest.raises(ToolUnknown):
        cloudtrail.handler(filter={"key": "BogusKey", "value": "x"})


def test_cloudtrail_max_events_capped():
    with pytest.raises(ToolUnknown):
        cloudtrail.handler(max_events=10_000)


def test_cloudtrail_window_capped_to_24h():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = []
    with patch("nexus.aws_client._client", return_value=fake):
        r = cloudtrail.handler(
            start_time="2026-04-25T00:00:00Z",
            end_time="2026-04-30T00:00:00Z",
        )
    assert r["window_capped_to_24h"] is True


# --- read_alb_logs --------------------------------------------------------

_ALB_LINE = (
    'https 2026-04-25T22:01:00.000000Z app/overwatch-v2-alb/x '
    '54.197.116.25:55000 10.100.10.239:9001 0.001 0.020 0.000 503 503 '
    '300 1500 "GET https://vaultscalerlabs.com:443/api/echo HTTP/1.1" '
    '"curl/7" - - '
    'arn:aws:elasticloadbalancing:us-east-1:418295677815:targetgroup/overwatch-v2-tg/abc '
    '"-" "vaultscalerlabs.com" "-" 0 '
    '2026-04-25T22:00:59.999000Z "forward" "-" "-" "10.100.10.239:9001" '
    '"503" "-" "-"'
)


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def test_alb_logs_rejects_unknown_bucket():
    with pytest.raises(ToolUnknown):
        alb_logs.handler(bucket="bogus-bucket")


def test_alb_logs_max_records_capped():
    with pytest.raises(ToolUnknown):
        alb_logs.handler(
            bucket="overwatch-v2-alb-logs-418295677815",
            max_records=10_000,
        )


def test_alb_logs_happy_path_parses_and_returns_records():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{
        "Contents": [{"Key": "AWSLogs/418295677815/elasticloadbalancing/us-east-1/2026/04/25/x.log.gz"}],
    }]
    fake.get_object.return_value = {"Body": MagicMock(read=lambda: _gz(_ALB_LINE + "\n"))}
    with patch("nexus.aws_client._client", return_value=fake):
        r = alb_logs.handler(
            bucket="overwatch-v2-alb-logs-418295677815",
            start_time="2026-04-25T22:00:00Z",
            end_time="2026-04-25T22:05:00Z",
            max_records=10,
        )
    assert r["total_count"] == 1
    assert r["records"][0]["elb_status_code"] == "503"


def test_alb_logs_status_prefix_filter_drops_2xx():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{
        "Contents": [{"Key": "x.log.gz"}],
    }]
    line2xx = _ALB_LINE.replace(" 503 503 ", " 200 200 ")
    body = "\n".join([_ALB_LINE, line2xx])
    fake.get_object.return_value = {"Body": MagicMock(read=lambda: _gz(body))}
    with patch("nexus.aws_client._client", return_value=fake):
        r = alb_logs.handler(
            bucket="overwatch-v2-alb-logs-418295677815",
            start_time="2026-04-25T22:00:00Z",
            end_time="2026-04-25T22:05:00Z",
            filter={"status_prefix": "5"},
        )
    assert r["total_count"] == 1
    assert r["records"][0]["elb_status_code"] == "503"


# --- read_cloudwatch_metrics ---------------------------------------------

def test_cw_metrics_requires_namespace_and_metric():
    with pytest.raises(ToolUnknown):
        cloudwatch_metrics.handler(namespace="X")
    with pytest.raises(ToolUnknown):
        cloudwatch_metrics.handler(metric_name="Y")


def test_cw_metrics_happy_path_average():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime(2026, 4, 26, 1, tzinfo=timezone.utc),
             "Average": 42.5, "Unit": "Count", "SampleCount": 10},
            {"Timestamp": datetime(2026, 4, 26, 1, 5, tzinfo=timezone.utc),
             "Average": 50.0, "Unit": "Count", "SampleCount": 12},
        ],
    }
    with patch("nexus.aws_client._client", return_value=fake):
        r = cloudwatch_metrics.handler(
            namespace="AWS/ApplicationELB", metric_name="HTTPCode_Target_5XX_Count",
            period_seconds=300, statistic="Sum",
        )
    assert r["total_count"] == 2
    assert r["datapoints"][0]["timestamp"].startswith("2026-04-26T01:00:00")


def test_cw_metrics_extended_statistic_uses_extended_arg():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {
        "Datapoints": [
            {"Timestamp": datetime.now(timezone.utc),
             "ExtendedStatistics": {"p95": 123.0}, "Unit": "Milliseconds",
             "SampleCount": 100},
        ],
    }
    with patch("nexus.aws_client._client", return_value=fake):
        r = cloudwatch_metrics.handler(
            namespace="AWS/ApplicationELB", metric_name="TargetResponseTime",
            statistic="p95",
        )
    assert r["datapoints"][0]["value"] == 123.0
    assert r["statistic"] == "p95"
    # Verify boto was called with ExtendedStatistics, not Statistics
    kwargs = fake.get_metric_statistics.call_args.kwargs
    assert "ExtendedStatistics" in kwargs and kwargs["ExtendedStatistics"] == ["p95"]


def test_cw_metrics_period_floor_enforced():
    fake = MagicMock()
    fake.get_metric_statistics.return_value = {"Datapoints": []}
    with patch("nexus.aws_client._client", return_value=fake):
        r = cloudwatch_metrics.handler(
            namespace="X", metric_name="Y", period_seconds=10,
        )
    assert r["period_seconds"] == 60   # floor


# --- query_correlated_events ----------------------------------------------

def test_correlated_events_window_capped():
    with pytest.raises(ToolUnknown):
        correlated_events.handler(window_seconds=99999)


def test_correlated_events_unknown_source_rejected():
    with pytest.raises(ToolUnknown):
        correlated_events.handler(sources=["cloudtrail", "bogus"])


def test_correlated_events_fans_out_to_three_sources():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{"Events": []}]
    fake.filter_log_events.return_value = {"events": []}
    fake.get_object.return_value = {"Body": MagicMock(read=lambda: b"")}
    with patch("nexus.aws_client._client", return_value=fake):
        r = correlated_events.handler(
            timestamp="2026-04-26T01:00:00Z",
            window_seconds=600,
            log_groups=["/aria/console"],
            alb_bucket="overwatch-v2-alb-logs-418295677815",
        )
    assert set(r["findings"].keys()) == {
        "cloudtrail", "cloudwatch_logs", "alb_logs",
    }
    assert r["window_seconds"] == 600


def test_correlated_events_cwlogs_requires_log_groups():
    fake = MagicMock()
    fake.get_paginator.return_value.paginate.return_value = [{"Events": []}]
    with patch("nexus.aws_client._client", return_value=fake):
        r = correlated_events.handler(
            sources=["cloudwatch_logs"], timestamp="2026-04-26T01:00:00Z",
        )
    assert "error" in r["findings"]["cloudwatch_logs"]


def test_correlated_events_alb_requires_bucket():
    fake = MagicMock()
    with patch("nexus.aws_client._client", return_value=fake):
        r = correlated_events.handler(
            sources=["alb_logs"], timestamp="2026-04-26T01:00:00Z",
        )
    assert "error" in r["findings"]["alb_logs"]

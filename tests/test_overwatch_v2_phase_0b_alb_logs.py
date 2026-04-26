"""Tests for Phase 0b read_alb_logs tool."""
import gzip
import io
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.overwatch_v2.tools.read_tools import read_alb_logs
from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown


SAMPLE_ALB_LINES = [
    'https 2026-04-26T13:30:01.123456Z app/overwatch-v2-alb/abc 10.0.0.1:443 10.0.0.2:9001 0.000 0.020 0.000 200 200 540 1234 "GET https://vaultscalerlabs.com:443/health HTTP/1.1" "curl/7.79" ECDHE-RSA-AES128-GCM-SHA256 TLSv1.2 arn:aws:elasticloadbalancing:us-east-1:418295677815:targetgroup/foo/abc "Root=1-x" "vaultscalerlabs.com" "arn:aws:acm:us-east-1:1:cert/x" 0 2026-04-26T13:30:01.000000Z "forward" "-" "-" "10.0.0.2:9001" "200" "-" "-" "-"',
    'https 2026-04-26T13:30:02.456789Z app/overwatch-v2-alb/abc 10.0.0.3:443 10.0.0.2:9001 0.000 0.030 0.000 502 502 0 100 "GET https://vaultscalerlabs.com:443/api/foo HTTP/1.1" "Mozilla/5.0" ECDHE-RSA-AES128-GCM-SHA256 TLSv1.2 arn:aws:elasticloadbalancing:us-east-1:418295677815:targetgroup/foo/abc "Root=1-y" "vaultscalerlabs.com" "arn:aws:acm:us-east-1:1:cert/x" 1 2026-04-26T13:30:02.000000Z "forward" "-" "-" "10.0.0.2:9001" "502" "-" "-" "-"',
]


def _gz_bytes(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(("\n".join(lines) + "\n").encode("utf-8"))
    return buf.getvalue()


def _mock_s3_with_one_object(monkeypatch, key: str, lines: list[str]):
    body_bytes = _gz_bytes(lines)
    fake = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [{"Key": key}]}]
    fake.get_paginator.return_value = paginator
    fake.head_object.return_value = {"ContentLength": len(body_bytes)}

    class _Body:
        def read(self) -> bytes: return body_bytes
    fake.get_object.return_value = {"Body": _Body()}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    return fake


KEY = ("AWSLogs/418295677815/elasticloadbalancing/us-east-1/2026/04/26/"
       "418295677815_elasticloadbalancing_us-east-1_app.overwatch-v2-alb"
       ".abc_20260426T1330Z_10.0.0.1_xyz.log.gz")


def test_default_window_60min(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, SAMPLE_ALB_LINES)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
    )
    assert r["source"] == "alb"
    assert r["count"] == 2


def test_min_status_filter(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, SAMPLE_ALB_LINES)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
        min_status=400,
    )
    assert r["count"] == 1
    assert r["events"][0]["elb_status_code"] == 502


def test_path_substring_filter(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, SAMPLE_ALB_LINES)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
        path_substring="/health",
    )
    assert r["count"] == 1
    assert "/health" in r["events"][0]["request"]


def test_alb_name_filter(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, SAMPLE_ALB_LINES)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
        alb_name="overwatch-v2-alb",
    )
    assert r["count"] == 2
    r2 = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
        alb_name="some-other-alb",
    )
    assert r2["count"] == 0


def test_window_capped_at_24h(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, [])
    r = read_alb_logs.handler(
        start_time="2026-04-26T00:00:00Z",
        end_time="2026-04-30T00:00:00Z",
    )
    s = datetime.fromisoformat(r["time_range"]["start"])
    e = datetime.fromisoformat(r["time_range"]["end"])
    assert (e - s) == timedelta(hours=24)
    assert r["truncated"] is True


def test_max_events_truncates(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, SAMPLE_ALB_LINES * 50)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
        max_events=10,
    )
    assert r["count"] == 10
    assert r["truncated"] is True


def test_skip_oversize_object(monkeypatch):
    fake = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [{"Key": KEY}]}]
    fake.get_paginator.return_value = paginator
    fake.head_object.return_value = {"ContentLength": 999_999_999}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
    )
    assert r["count"] == 0
    fake.get_object.assert_not_called()


def test_filename_outside_window_skipped(monkeypatch):
    """File timestamp far outside window must not be fetched."""
    far_key = ("AWSLogs/418295677815/elasticloadbalancing/us-east-1/2026/04/26/"
               "...x_20260426T0100Z_x_y.log.gz")
    fake = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Contents": [{"Key": far_key}]}]
    fake.get_paginator.return_value = paginator
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
    )
    assert r["count"] == 0
    fake.head_object.assert_not_called()


def test_bad_iso_raises():
    with pytest.raises(ToolUnknown, match="ISO-8601"):
        read_alb_logs.handler(start_time="bad")


def test_records_have_expected_fields(monkeypatch):
    _mock_s3_with_one_object(monkeypatch, KEY, [SAMPLE_ALB_LINES[0]])
    r = read_alb_logs.handler(
        start_time="2026-04-26T13:25:00Z",
        end_time="2026-04-26T13:35:00Z",
    )
    rec = r["events"][0]
    for k in ("type", "timestamp", "alb", "client_addr", "elb_status_code",
              "request", "user_agent", "target_group_arn"):
        assert k in rec
    assert rec["elb_status_code"] == 200

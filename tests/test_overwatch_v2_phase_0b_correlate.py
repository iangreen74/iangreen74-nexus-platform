"""Tests for Phase 0b query_correlated_events tool."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("NEXUS_MODE", "local")

import pytest

from nexus.overwatch_v2.tools.read_tools import (
    query_correlated_events as correlate,
    read_alb_logs, read_cloudtrail,
    cloudwatch_logs as cw_logs,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import ToolUnknown


CENTRE = "2026-04-26T13:30:00Z"


def test_default_window_60s(monkeypatch):
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": []})
    r = correlate.handler(timestamp=CENTRE)
    assert r["window_seconds"] == 60
    s = datetime.fromisoformat(r["time_range"]["start"])
    e = datetime.fromisoformat(r["time_range"]["end"])
    assert (e - s) == timedelta(seconds=120)


def test_window_capped_at_600s(monkeypatch):
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": []})
    r = correlate.handler(timestamp=CENTRE, window_seconds=99999)
    assert r["window_seconds"] == 600


def test_unknown_source_rejected():
    with pytest.raises(ToolUnknown, match="unknown source"):
        correlate.handler(timestamp=CENTRE, sources=["cloudtrail", "made-up"])


def test_normalises_three_sources(monkeypatch):
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": [
        {"timestamp": "2026-04-26T13:29:55Z", "event_name": "UpdateService",
         "principal": "ian", "resources": [{"type": "x", "name": "n1"}],
         "raw": {"a": 1}},
    ]})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": [
        {"timestamp": "2026-04-26T13:30:01Z", "request": "GET /health HTTP/1.1",
         "elb_status_code": 200, "client_addr": "10.0.0.1",
         "target_group_arn": "arn:tg/x"},
    ]})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": [
        {"timestamp": "2026-04-26T13:30:05Z", "message": "starting up"},
    ]})
    r = correlate.handler(timestamp=CENTRE,
                          log_group="/aws/ecs/aria-console")
    assert r["count"] == 3
    sources = [ev["source"] for ev in r["events"]]
    assert set(sources) == {"cloudtrail", "alb", "cloudwatch_logs"}


def test_time_sorted_output(monkeypatch):
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": [
        {"timestamp": "2026-04-26T13:30:30Z", "event_name": "X",
         "principal": None, "resources": [], "raw": {}},
    ]})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": [
        {"timestamp": "2026-04-26T13:29:55Z", "request": "GET /a HTTP/1.1",
         "elb_status_code": 200},
    ]})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": []})
    r = correlate.handler(timestamp=CENTRE,
                          sources=["cloudtrail", "alb"])
    timestamps = [ev["timestamp"] for ev in r["events"]]
    assert timestamps == sorted(timestamps)


def test_subset_of_sources(monkeypatch):
    called: list[str] = []

    def _ct(**kw):
        called.append("ct")
        return {"events": []}

    def _alb(**kw):
        called.append("alb")
        return {"events": []}

    def _cw(**kw):
        called.append("cw")
        return {"events": []}

    monkeypatch.setattr(read_cloudtrail, "handler", _ct)
    monkeypatch.setattr(read_alb_logs, "handler", _alb)
    monkeypatch.setattr(cw_logs, "handler", _cw)
    correlate.handler(timestamp=CENTRE, sources=["cloudtrail"])
    assert called == ["ct"]


def test_empty_streams(monkeypatch):
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": []})
    r = correlate.handler(timestamp=CENTRE)
    assert r["count"] == 0
    assert r["events"] == []


def test_total_cap_truncates(monkeypatch):
    big_alb = [{"timestamp": f"2026-04-26T13:30:0{i}Z",
                "request": "GET / HTTP/1.1", "elb_status_code": 200}
               for i in range(9)]
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": big_alb})
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(cw_logs, "handler", lambda **kw: {"events": []})
    r = correlate.handler(timestamp=CENTRE, sources=["alb"], max_events=3)
    assert r["count"] == 3
    assert r["truncated"] is True


def test_cloudwatch_logs_skipped_without_log_group(monkeypatch):
    """If sources includes cloudwatch_logs but no log_group given, no error
    just an empty contribution from that source."""
    monkeypatch.setattr(read_cloudtrail, "handler", lambda **kw: {"events": []})
    monkeypatch.setattr(read_alb_logs, "handler", lambda **kw: {"events": []})
    called = {"cw": 0}

    def _cw(**kw):
        called["cw"] += 1
        return {"events": []}
    monkeypatch.setattr(cw_logs, "handler", _cw)
    correlate.handler(timestamp=CENTRE)  # no log_group
    assert called["cw"] == 0


def test_bad_centre_iso_raises():
    with pytest.raises(ToolUnknown, match="ISO-8601"):
        correlate.handler(timestamp="not-a-time")

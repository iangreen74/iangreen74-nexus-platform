"""Phase 0b dogfood: substrate proves itself by correlating its own deploy.

Per the override anchor: 'What happened across all systems in the 15
minutes following Phase 0b's own deploy?' This test verifies the
correlation primitive composes the three read-* tools into a coherent
timeline. Uses synthetic event streams (mocked AWS calls) so the test
runs deterministically — the real production probe is the
post-deploy operator question.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.overwatch_v2.tools.read_tools import (
    cloudwatch_logs as cw_logs,
    query_correlated_events as correlate,
    read_alb_logs, read_cloudtrail,
)


# Imagined Phase 0b deploy timeline:
#   T+00s: aws ecs update-service (CloudTrail)
#   T+05s: ALB target draining (ALB log: 503 to /health)
#   T+12s: container starts (CW logs: 'starting up')
#   T+25s: ALB target healthy (ALB log: 200 to /health)
#   T+30s: deployment marker (CW logs: 'register_tool: read_cloudtrail')
DEPLOY_T = datetime(2026, 4, 26, 14, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def mock_three_sources(monkeypatch):
    def _ct(**kw):
        return {"events": [
            {
                "timestamp": DEPLOY_T.isoformat(),
                "event_id": "evt-update-service",
                "event_name": "UpdateService",
                "principal": "ian-myceliux",
                "resources": [{"type": "AWS::ECS::Service",
                               "name": "arn:aws:ecs:us-east-1:418295677815:service/overwatch-platform/aria-console"}],
                "raw": "{...}",
            },
        ]}

    def _alb(**kw):
        return {"events": [
            {"timestamp": (DEPLOY_T + timedelta(seconds=5)).isoformat(),
             "request": "GET /health HTTP/1.1", "elb_status_code": 503,
             "client_addr": "10.0.0.1", "alb": "app/overwatch-v2-alb/x",
             "target_group_arn": "arn:tg/aria-console-tg"},
            {"timestamp": (DEPLOY_T + timedelta(seconds=25)).isoformat(),
             "request": "GET /health HTTP/1.1", "elb_status_code": 200,
             "client_addr": "10.0.0.1", "alb": "app/overwatch-v2-alb/x",
             "target_group_arn": "arn:tg/aria-console-tg"},
        ]}

    def _cw(**kw):
        return {"events": [
            {"timestamp": (DEPLOY_T + timedelta(seconds=12)).isoformat(),
             "message": "starting up uvicorn on :9001"},
            {"timestamp": (DEPLOY_T + timedelta(seconds=30)).isoformat(),
             "message": "register_tool: read_cloudtrail registered"},
        ]}

    monkeypatch.setattr(read_cloudtrail, "handler", _ct)
    monkeypatch.setattr(read_alb_logs, "handler", _alb)
    monkeypatch.setattr(cw_logs, "handler", _cw)


def test_substrate_correlates_its_own_deploy(mock_three_sources):
    """The substrate proves itself: correlator returns a coherent timeline."""
    centre = (DEPLOY_T + timedelta(seconds=20)).isoformat()
    r = correlate.handler(
        timestamp=centre,
        window_seconds=60,
        log_group="/aws/ecs/aria-console",
    )
    assert r["count"] == 5
    assert set(r["by_source"]) == {"cloudtrail", "alb", "cloudwatch_logs"}
    assert r["by_source"]["cloudtrail"] == 1
    assert r["by_source"]["alb"] == 2
    assert r["by_source"]["cloudwatch_logs"] == 2

    # Time-sorted output is the proof of correlation.
    timestamps = [ev["timestamp"] for ev in r["events"]]
    assert timestamps == sorted(timestamps), \
        "events must be time-sorted for correlation to be meaningful"

    # The chain is reconstructible: deploy -> drain -> startup -> healthy -> ready
    summaries = [ev["summary"] for ev in r["events"]]
    assert "UpdateService" in summaries[0]
    assert "503" in summaries[1]
    assert "starting up" in summaries[2]
    assert "200" in summaries[3]
    assert "register_tool" in summaries[4]


def test_substrate_returns_evidence_locator_per_event(mock_three_sources):
    """Every row must carry the raw payload so a human can re-fetch."""
    r = correlate.handler(
        timestamp=DEPLOY_T.isoformat(),
        window_seconds=60,
        log_group="/aws/ecs/aria-console",
    )
    for ev in r["events"]:
        assert "raw" in ev
        assert ev["source"] in {"cloudtrail", "alb", "cloudwatch_logs"}
        assert ev["timestamp"] is not None


def test_substrate_subset_call_works(mock_three_sources):
    """Operator can scope to a subset of sources and still get a timeline."""
    r = correlate.handler(
        timestamp=DEPLOY_T.isoformat(),
        window_seconds=60,
        sources=["cloudtrail", "alb"],
    )
    assert set(r["by_source"]) == {"cloudtrail", "alb"}
    assert "cloudwatch_logs" not in r["by_source"]
    sources = {ev["source"] for ev in r["events"]}
    assert sources == {"cloudtrail", "alb"}

"""Tests for the Phase 0b.5.1 UnifiedEvent helper module.

Schema invariants + round-trip mappers verified against real fixtures
captured from production on 2026-04-27 (CT UpdateService event, ALB
request to vaultscalerlabs.com/engineering/, CW ERROR log from the
aria-console service). PII scrubbed: ALB client IP -> 1.2.3.4, CT STS
accessKeyId redacted; no userName fields contained human names.

Tests live in top-level tests/ rather than nexus/echo/tests/ because
CI runs ``pytest tests/`` only — keeping fixtures alongside other
overwatch_v2 phase-0b test data.
"""
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from nexus.echo._unified_events import (
    UnifiedEvent,
    from_alb,
    from_cloudtrail,
    from_cloudwatch,
)


FIXTURES = Path(__file__).parent / "fixtures" / "echo"
NOW = datetime(2026, 4, 27, tzinfo=timezone.utc)


def test_unified_event_is_frozen():
    """Mutation should fail — events flow through correlation bucketing
    where shared references must not drift."""
    e = UnifiedEvent(source="cloudtrail", timestamp=NOW, action="x")
    with pytest.raises(ValidationError):
        e.action = "y"


def test_unified_event_is_hashable():
    """Hashable so events can be dict keys / set members during the
    correlation-key bucketing pass (Phase 0b.5.3)."""
    e = UnifiedEvent(source="cloudtrail", timestamp=NOW, action="x")
    assert hash(e) is not None
    assert e in {e}


def test_unified_event_status_default_unknown():
    e = UnifiedEvent(source="cloudtrail", timestamp=NOW, action="x")
    assert e.status == "unknown"


def test_cloudtrail_mapper_against_real_fixture():
    """Round-trip a real UpdateService event captured 2026-04-27 from the
    aria-console deploy. Asserts the fields we depend on for synthesis
    survive intact, not just that the function returns something."""
    raw = json.loads((FIXTURES / "cloudtrail_update_service.json").read_text())
    ev = from_cloudtrail(raw)
    assert ev.source == "cloudtrail"
    assert ev.action == "UpdateService"
    assert ev.actor and ev.actor.startswith("arn:aws:sts::418295677815:")
    assert ev.status == "success"
    assert "request_id" in ev.correlation_keys
    assert "aws_request_id" in ev.correlation_keys
    assert ev.timestamp.tzinfo is not None
    assert ev.raw == raw


def test_cloudtrail_mapper_principalId_fallback_when_no_arn():
    """userIdentity.arn absent -> fall back to principalId. Hits the
    actor-extraction branch the real-fixture test doesn't exercise."""
    raw = {
        "eventTime": "2026-04-27T00:00:00Z",
        "eventName": "AssumeRole",
        "eventID": "evt-1",
        "userIdentity": {"principalId": "AIDAEXAMPLE"},
    }
    ev = from_cloudtrail(raw)
    assert ev.actor == "AIDAEXAMPLE"


def test_cloudtrail_mapper_errorCode_marks_failure():
    raw = {
        "eventTime": "2026-04-27T00:00:00Z",
        "eventName": "UpdateService",
        "eventID": "evt-1",
        "errorCode": "AccessDenied",
        "userIdentity": {"arn": "arn:aws:iam::1:user/x"},
    }
    ev = from_cloudtrail(raw)
    assert ev.status == "failure"


def test_alb_mapper_against_real_fixture():
    """Round-trip a real 200 OK request to vaultscalerlabs.com/engineering.
    Client IP scrubbed to 1.2.3.4 in the fixture."""
    line = (FIXTURES / "alb_request.txt").read_text().strip()
    ev = from_alb(line)
    assert ev.source == "alb"
    assert ev.actor == "1.2.3.4"
    assert ev.target == "/engineering/"
    assert ev.action == "GET /engineering/"
    assert ev.status == "success"
    assert ev.correlation_keys["xray_trace_id"].startswith("1-")
    assert ev.timestamp.tzinfo is not None


def test_alb_mapper_classifies_400s_as_failure():
    line = (
        'h2 2026-04-27T00:00:00.000000Z app/x/y 1.2.3.4:1 10.0.0.1:80 '
        '0 0 0 503 503 0 0 "GET https://x/y HTTP/2.0" "ua" '
        'TLS_AES TLSv1.3 arn:aws:elasticloadbalancing:us-east-1:1:targetgroup/x/y '
        '"Root=1-abc-def" "x" "arn:aws:acm:us-east-1:1:certificate/x" 0 '
        '2026-04-27T00:00:00.000000Z "forward" "-" "-" "10.0.0.1:80" "503" "-" "-"'
    )
    ev = from_alb(line)
    assert ev.status == "failure"


def test_alb_field_count_at_least_22():
    """Schema-drift guard. ALB v2 format has been stable but if AWS
    truncates fields or reorders, the positional indices the mapper
    relies on (timestamp [1], client [3], status [8], request [12],
    trace [17]) silently shift. Assert the fixture still has the field
    count we wrote the mapper against — failure here means refresh
    the fixture and re-verify the indices."""
    line = (FIXTURES / "alb_request.txt").read_text().strip()
    fields = shlex.split(line)
    assert len(fields) >= 22, (
        f"ALB log has {len(fields)} fields; mapper assumes >=22. "
        "AWS may have changed format; refresh fixture and verify indices "
        "against https://docs.aws.amazon.com/elasticloadbalancing/latest/"
        "application/load-balancer-access-logs.html"
    )


def test_cloudwatch_mapper_against_real_fixture():
    """Round-trip a real ERROR event from /aria/console. Asserts ERROR
    keyword -> failure status and that the message survives in raw."""
    raw = json.loads((FIXTURES / "cloudwatch_event.json").read_text())
    ev = from_cloudwatch(raw, log_group="/aria/console")
    assert ev.source == "cloudwatch"
    assert ev.target == "/aria/console"
    assert ev.actor.startswith("/aria/console:")
    assert ev.status == "failure"
    assert "ERROR" in ev.action
    assert ev.raw == raw


def test_cloudwatch_mapper_extracts_tenant():
    """Synthetic test for tenant-id regex extraction. Real fixture above
    doesn't contain a tenant tag; this exercises the correlation path."""
    raw = {
        "timestamp": 1761511200000,
        "message": "info: handling request for forge-1dba4143ca24ed1f",
        "logStreamName": "test/123",
    }
    ev = from_cloudwatch(raw, log_group="/aws/ecs/forgescaler")
    assert ev.correlation_keys["tenant_id"] == "forge-1dba4143ca24ed1f"
    assert ev.status == "success"


def test_cloudwatch_mapper_extracts_request_id_from_json_message():
    raw = {
        "timestamp": 1761511200000,
        "message": '{"level": "info", "request_id": "req-abc-123", "msg": "ok"}',
        "logStreamName": "test/123",
    }
    ev = from_cloudwatch(raw, log_group="/aws/ecs/forgescaler")
    assert ev.correlation_keys["request_id"] == "req-abc-123"


def test_cloudwatch_mapper_unknown_status_when_no_keywords():
    raw = {
        "timestamp": 1761511200000,
        "message": "10.0.0.1 - - request handled",
        "logStreamName": "x",
    }
    ev = from_cloudwatch(raw, log_group="/x")
    assert ev.status == "unknown"

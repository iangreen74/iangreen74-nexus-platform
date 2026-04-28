"""UnifiedEvent helper module — Phase 0b.5.1.

Normalizes events from CloudTrail, ALB access logs, and CloudWatch logs
into a single shape so cross-source queries (Phase 0b.5.5's
``query_unified_events`` tool) and correlation bucketing (0b.5.3) can
reason over them uniformly. Existing 0b read tools stay frozen — this
module is a separate synthesis layer they can be passed through.

Refs: /tmp/phase_0b5_design_20260426_1803.md §4.1, §4.2.
"""
from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


SourceLiteral = Literal["cloudtrail", "alb", "cloudwatch"]
StatusLiteral = Literal["success", "failure", "unknown"]


class UnifiedEvent(BaseModel):
    """Cross-source normalized event.

    Frozen so events are hashable and usable as dict keys during the
    correlation-bucketing pass (0b.5.3). ``raw`` preserves the source
    payload verbatim for the [expand] panel — only ``action``,
    ``actor``, ``target``, and ``correlation_keys`` are interpreted.
    """
    model_config = ConfigDict(frozen=True)

    source: SourceLiteral
    timestamp: datetime
    actor: str | None = None
    target: str | None = None
    action: str
    status: StatusLiteral = "unknown"
    correlation_keys: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((
            self.source, self.timestamp, self.actor, self.target,
            self.action, self.status,
            tuple(sorted(self.correlation_keys.items())),
        ))


def _parse_iso(s: str) -> datetime:
    """Parse ISO-8601 timestamps from CT (Z-suffixed) and ALB (microseconds
    + Z). Returns UTC-aware datetime."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def from_cloudtrail(event: dict[str, Any]) -> UnifiedEvent:
    """Map a CloudTrail event (parsed inner ``CloudTrailEvent`` JSON) to
    UnifiedEvent.

    Field paths verified against a real production UpdateService event
    captured 2026-04-27 (see tests/fixtures/echo/cloudtrail_update_service.json).
    The inner event has no ``resources`` field for ECS Update calls —
    target falls back to None in that case; callers that want the
    affected resource ARN should look at ``raw['responseElements']``.
    """
    user_identity = event.get("userIdentity") or {}
    actor = user_identity.get("arn") or user_identity.get("principalId")

    target: str | None = None
    resources = event.get("resources") or []
    if resources:
        target = resources[0].get("ARN")

    status: StatusLiteral = "failure" if event.get("errorCode") else "success"

    correlation_keys: dict[str, str] = {}
    if eid := event.get("eventID"):
        correlation_keys["request_id"] = eid
    if rid := event.get("requestID"):
        correlation_keys["aws_request_id"] = rid

    return UnifiedEvent(
        source="cloudtrail",
        timestamp=_parse_iso(event["eventTime"]),
        actor=actor,
        target=target,
        action=event["eventName"],
        status=status,
        correlation_keys=correlation_keys,
        raw=event,
    )


def from_alb(log_line: str) -> UnifiedEvent:
    """Map a single ALB v2 access log line to UnifiedEvent.

    Field positions (verified 2026-04-27 against a real
    vaultscalerlabs.com request — see tests/fixtures/echo/alb_request.txt):
      [1] timestamp, [3] client:port, [8] elb_status_code,
      [12] request, [17] trace_id.
    Trace ID is at [17], not [22] — the design doc's worked example
    was off; updated per real fixture.
    Format reference: AWS ALB Access Log Entry Syntax docs.
    """
    fields = shlex.split(log_line)
    timestamp = _parse_iso(fields[1])
    client_addr = fields[3].rsplit(":", 1)[0]
    elb_status = fields[8]
    request = fields[12]
    trace_id = fields[17] if len(fields) > 17 else ""

    parts = request.split()
    verb = parts[0] if parts else "?"
    full_url = parts[1] if len(parts) >= 2 else ""
    path = urlparse(full_url).path or full_url or "/"

    code = int(elb_status) if elb_status.isdigit() else 0
    status: StatusLiteral
    if 200 <= code < 400:
        status = "success"
    elif code >= 400:
        status = "failure"
    else:
        status = "unknown"

    correlation_keys: dict[str, str] = {}
    if trace_id.startswith("Root="):
        correlation_keys["xray_trace_id"] = trace_id.removeprefix("Root=")

    return UnifiedEvent(
        source="alb",
        timestamp=timestamp,
        actor=client_addr,
        target=path,
        action=f"{verb} {path}",
        status=status,
        correlation_keys=correlation_keys,
        raw={"line": log_line, "fields": fields},
    )


_TENANT_RE = re.compile(r"\b(forge-[0-9a-f]{16})\b")
_REQUEST_ID_KEYS = ("request_id", "requestId", "trace_id", "x-request-id")


def from_cloudwatch(event: dict[str, Any], log_group: str) -> UnifiedEvent:
    """Map a CloudWatch Logs event (as returned by ``filter_log_events``)
    to UnifiedEvent.

    CW events carry less structure than CT/ALB. Status is inferred
    best-effort from message content (ERROR/WARN/exception/traceback →
    failure, INFO/OK → success, otherwise unknown). The full message
    stays in ``raw`` — ``action`` is intentionally truncated to keep
    the unified view scannable.
    """
    msg = event.get("message", "")
    ts = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
    log_stream = event.get("logStreamName", "?")

    correlation_keys: dict[str, str] = {}
    if m := _TENANT_RE.search(msg):
        correlation_keys["tenant_id"] = m.group(1)
    try:
        parsed = json.loads(msg)
        if isinstance(parsed, dict):
            for key in _REQUEST_ID_KEYS:
                if key in parsed:
                    correlation_keys["request_id"] = str(parsed[key])
                    break
    except (json.JSONDecodeError, ValueError):
        pass

    status: StatusLiteral
    msg_lower = msg.lower()
    if any(t in msg_lower for t in ("error", "exception", "traceback", "warn")):
        status = "failure"
    elif "info" in msg_lower or " ok" in msg_lower:
        status = "success"
    else:
        status = "unknown"

    return UnifiedEvent(
        source="cloudwatch",
        timestamp=ts,
        actor=f"{log_group}:{log_stream}",
        target=log_group,
        action=msg[:80].replace("\n", " "),
        status=status,
        correlation_keys=correlation_keys,
        raw=event,
    )

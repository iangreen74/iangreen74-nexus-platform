"""Production sink for the Track F tool registry's _emit_audit hook.

Until this module existed the registry's audit branch silently no-op'd
in production (the registry imports `emit_action_event` lazily and
swallows ImportError into a logged warning). Phase 0b ships this module
+ the matching log group (`infra/overwatch-v2/16-operator-substrate-
audit-logs.yml`) + the IAM grant (`OperatorSubstrateAuditWrite` Sid in
03-iam-reasoner-role.yml).

One log stream per actor; one JSON event per dispatched tool call.
Audit failure is non-fatal — registry's _emit_audit catches and logs.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from nexus.config import MODE

log = logging.getLogger("nexus.overwatch_v2.audit")

LOG_GROUP = "/overwatch-v2/operator-substrate-audit"


def _stream_name(actor: str) -> str:
    """One stream per actor keeps related events grouped + scannable."""
    return actor or "unknown-actor"


def emit_action_event(record: dict[str, Any]) -> None:
    """Append one structured audit row to /overwatch-v2/operator-substrate-audit.

    The record shape comes from registry._emit_audit:
      audit_id, tool_name, actor, parameters, ok, error, duration_ms,
      approval_token_id, ts_unix_ms.

    Local mode: no-op (registry uses _local_audit_log instead). Production:
    write to CW Logs. Stream is auto-created on first write.
    """
    if MODE != "production":
        return
    actor = record.get("actor") or "unknown-actor"
    stream = _stream_name(actor)
    try:
        from nexus.aws_client import _client as factory
        logs = factory("logs")
        try:
            logs.put_log_events(
                logGroupName=LOG_GROUP,
                logStreamName=stream,
                logEvents=[{
                    "timestamp": int(record.get("ts_unix_ms") or time.time() * 1000),
                    "message": json.dumps(record, default=str),
                }],
            )
        except logs.exceptions.ResourceNotFoundException:
            logs.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream)
            logs.put_log_events(
                logGroupName=LOG_GROUP,
                logStreamName=stream,
                logEvents=[{
                    "timestamp": int(record.get("ts_unix_ms") or time.time() * 1000),
                    "message": json.dumps(record, default=str),
                }],
            )
    except Exception:
        log.exception("operator-substrate-audit emit failed for %s",
                      record.get("tool_name"))
        raise

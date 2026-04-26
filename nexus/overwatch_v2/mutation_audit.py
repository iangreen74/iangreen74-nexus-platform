"""Phase 1 mutation-audit fan-out from registry.dispatch.

Fires one structured JSON event per dispatch attempt against any tool
with requires_approval=True, regardless of outcome:
  - rejected_no_token  — caller didn't supply a token
  - rejected_bad_token — verify_token returned valid=False
  - success            — handler completed without raising
  - tool_error         — handler raised; mutation may have partially fired

Sink: /overwatch-v2/echo-mutations (365-day retention, provisioned by
infra/overwatch-v2/17-echo-mutations-audit-logs.yml). Distinct from
operator-substrate-audit (90d, every dispatch) so mutation history is
the long-term audit trail.

Audit failure is non-fatal — registry's caller should not block tool
execution on audit-write failure. The module raises on AWS-side
errors so the registry can choose its own swallow policy.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from nexus.config import MODE

log = logging.getLogger("nexus.overwatch_v2.mutation_audit")

LOG_GROUP = "/overwatch-v2/echo-mutations"

# Outcome enum — match the dispatch hook's classification.
OUTCOME_REJECTED_NO_TOKEN = "rejected_no_token"
OUTCOME_REJECTED_BAD_TOKEN = "rejected_bad_token"
OUTCOME_SUCCESS = "success"
OUTCOME_TOOL_ERROR = "tool_error"


def _stream_name(actor: str) -> str:
    """One stream per actor + UTC date so grep stays cheap."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return f"{actor or 'unknown-actor'}/{today}"


def emit_mutation_event(
    *,
    tool_name: str,
    parameters: dict[str, Any],
    actor: str,
    outcome: str,
    token_id_prefix: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """Append one structured row to /overwatch-v2/echo-mutations.

    parameters is captured by KEY only (values may carry sensitive payload
    data — body of a PR comment, etc). Full parameters are observable in
    the operator-substrate-audit stream which is access-scoped.
    """
    record: dict[str, Any] = {
        "ts_unix_ms": int(time.time() * 1000),
        "tool_name": tool_name,
        "actor": actor,
        "outcome": outcome,
        "param_keys": sorted(list(parameters.keys())),
        "token_id_prefix": token_id_prefix,
        "duration_ms": duration_ms,
    }
    if error is not None:
        record["error"] = str(error)[:500]
    if MODE != "production":
        log.debug("mutation-audit (local): %s", json.dumps(record))
        return
    actor_for_stream = actor or "unknown-actor"
    stream = _stream_name(actor_for_stream)
    try:
        from nexus.aws_client import _client as factory
        logs = factory("logs")
        try:
            logs.put_log_events(
                logGroupName=LOG_GROUP,
                logStreamName=stream,
                logEvents=[{
                    "timestamp": record["ts_unix_ms"],
                    "message": json.dumps(record, default=str),
                }],
            )
        except logs.exceptions.ResourceNotFoundException:
            logs.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream)
            logs.put_log_events(
                logGroupName=LOG_GROUP,
                logStreamName=stream,
                logEvents=[{
                    "timestamp": record["ts_unix_ms"],
                    "message": json.dumps(record, default=str),
                }],
            )
    except Exception:
        log.exception("echo-mutations audit emit failed for %s", tool_name)
        raise

"""Step Functions probe for dogfood diagnostic reports.

Reads execution state from the forgewing-deploy-v2 state machine.
All functions return empty/error dicts on any exception (never raise).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("nexus.intelligence.dogfood_sfn_probe")

_DEFAULT_ARN = (
    "arn:aws:states:us-east-1:418295677815:stateMachine:forgewing-deploy-v2"
)
STATE_MACHINE_ARN = os.environ.get("V2_STATE_MACHINE_ARN", _DEFAULT_ARN)

_sfn_client = None


def _sfn():
    """Lazy boto3 Step Functions client."""
    global _sfn_client
    if _sfn_client is None:
        import boto3
        _sfn_client = boto3.client("stepfunctions", region_name="us-east-1")
    return _sfn_client


def _to_iso(dt: Any) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def list_recent_executions(
    hours: int = 6,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent SFN executions filtered by time window.

    Returns a list of execution summary dicts, newest first.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        response = _sfn().list_executions(
            stateMachineArn=STATE_MACHINE_ARN,
            maxResults=min(limit, 100),
        )
    except Exception:
        logger.exception("list_recent_executions failed")
        return []

    results: list[dict[str, Any]] = []
    for ex in response.get("executions", []):
        start = ex.get("startDate")
        if start and start.tzinfo and start < cutoff:
            continue
        stop = ex.get("stopDate")
        dur_ms = (
            int((stop - start).total_seconds() * 1000)
            if start and stop else None
        )
        results.append({
            "execution_arn": ex.get("executionArn"),
            "name": ex.get("name"),
            "status": ex.get("status"),
            "start_date": _to_iso(start),
            "stop_date": _to_iso(stop),
            "duration_ms": dur_ms,
        })
    return results[:limit]


def describe_execution(arn: str) -> dict[str, Any]:
    """Describe a single SFN execution by ARN."""
    try:
        resp = _sfn().describe_execution(executionArn=arn)
        return {
            "execution_arn": resp.get("executionArn"),
            "status": resp.get("status"),
            "name": resp.get("name"),
            "start_date": _to_iso(resp.get("startDate")),
            "stop_date": _to_iso(resp.get("stopDate")),
            "input": resp.get("input"),
            "output": resp.get("output"),
            "error": resp.get("error"),
            "cause": resp.get("cause"),
        }
    except Exception:
        logger.exception("describe_execution failed: %s", arn)
        return {"execution_arn": arn, "error": "describe failed"}


def get_execution_terminal_state(arn: str) -> dict[str, Any]:
    """Walk execution history to find which state terminated and why.

    Returns a dict with terminal_state, error, and cause fields.
    """
    try:
        resp = _sfn().get_execution_history(
            executionArn=arn,
            maxResults=500,
            includeExecutionData=True,
            reverseOrder=True,
        )
    except Exception:
        logger.exception("get_execution_terminal_state failed: %s", arn)
        return {"execution_arn": arn, "error": "history fetch failed"}

    terminal_state: str | None = None
    error: str | None = None
    cause: str | None = None

    for ev in resp.get("events", []):
        ev_type = ev.get("type", "")
        # Find the last failed or aborted state
        if "Failed" in ev_type or "Aborted" in ev_type or "TimedOut" in ev_type:
            for detail_key in (
                "taskFailedEventDetails",
                "executionFailedEventDetails",
                "executionAbortedEventDetails",
                "executionTimedOutEventDetails",
                "lambdaFunctionFailedEventDetails",
            ):
                details = ev.get(detail_key)
                if details:
                    error = error or details.get("error")
                    cause = cause or details.get("cause")
        if "stateEnteredEventDetails" in ev and terminal_state is None:
            entered = ev.get("stateEnteredEventDetails", {})
            terminal_state = entered.get("name")

    return {
        "execution_arn": arn,
        "terminal_state": terminal_state,
        "error": error,
        "cause": cause,
    }

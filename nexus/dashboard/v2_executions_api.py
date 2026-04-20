"""v2 Step Functions Execution Trace API.

Surfaces forgewing-deploy-v2 state machine executions for Overwatch.

GET /api/v2-executions — list recent executions
GET /api/v2-executions/{arn}/history — state-by-state timeline

Reads from AWS Step Functions API (source of truth for execution state).
Read-only. Graceful on AWS errors (empty list, not 500).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("nexus.dashboard.v2_executions")

router = APIRouter(prefix="/api/v2-executions", tags=["v2-executions"])

_DEFAULT_ARN = "arn:aws:states:us-east-1:418295677815:stateMachine:forgewing-deploy-v2"
STATE_MACHINE_ARN = os.environ.get("V2_STATE_MACHINE_ARN", _DEFAULT_ARN)
_sfn_client = None


def _sfn():
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions", region_name="us-east-1")
    return _sfn_client


def _to_iso(dt):
    if dt is None:
        return None
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


@router.get("")
async def list_executions(
    status_filter: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    """List recent v2 state machine executions."""
    valid = {"RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
    if status_filter is not None and status_filter not in valid:
        raise HTTPException(status_code=400,
                            detail=f"status_filter must be one of {sorted(valid)}")
    try:
        params: dict[str, Any] = {"stateMachineArn": STATE_MACHINE_ARN, "maxResults": limit}
        if status_filter:
            params["statusFilter"] = status_filter
        response = _sfn().list_executions(**params)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.warning("list_executions failed (%s): %s", code, e)
        return {"state_machine_arn": STATE_MACHINE_ARN, "executions": [], "count": 0,
                "error": f"AWS error: {code}",
                "error_detail": "State machine may not be deployed yet (CFN wire-up pending)"}
    except Exception as e:
        logger.error("list_executions unexpected: %s", e)
        return {"state_machine_arn": STATE_MACHINE_ARN, "executions": [], "count": 0,
                "error": str(e)}

    executions = []
    for ex in response.get("executions", []):
        start, stop = ex.get("startDate"), ex.get("stopDate")
        dur = int((stop - start).total_seconds() * 1000) if start and stop else None
        executions.append({
            "execution_arn": ex.get("executionArn"), "name": ex.get("name"),
            "status": ex.get("status"), "start_date": _to_iso(start),
            "stop_date": _to_iso(stop), "duration_ms": dur,
        })
    return {"state_machine_arn": STATE_MACHINE_ARN,
            "executions": executions, "count": len(executions)}


@router.get("/{execution_arn:path}/history")
async def execution_history(
    execution_arn: str,
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    """State-by-state event timeline for a specific execution."""
    try:
        response = _sfn().get_execution_history(
            executionArn=execution_arn, maxResults=limit,
            includeExecutionData=True, reverseOrder=False)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "Unknown")
        if code in ("ExecutionDoesNotExist", "InvalidArn"):
            raise HTTPException(status_code=404, detail=f"{code}: {e}")
        logger.warning("get_execution_history failed (%s): %s", code, e)
        return {"execution_arn": execution_arn, "events": [], "count": 0,
                "error": f"AWS error: {code}"}
    except Exception as e:
        logger.error("get_execution_history unexpected: %s", e)
        return {"execution_arn": execution_arn, "events": [], "count": 0,
                "error": str(e)}

    events = []
    for ev in response.get("events", []):
        events.append({
            "id": ev.get("id"), "type": ev.get("type"),
            "timestamp": _to_iso(ev.get("timestamp")),
            "state_entered": (ev.get("stateEnteredEventDetails") or {}).get("name"),
            "state_exited": (ev.get("stateExitedEventDetails") or {}).get("name"),
            "task_failed": (ev.get("taskFailedEventDetails") or {}).get("error"),
            "lambda_function_failed": (ev.get("lambdaFunctionFailedEventDetails") or {}).get("error"),
        })
    return {"execution_arn": execution_arn, "events": events, "count": len(events)}

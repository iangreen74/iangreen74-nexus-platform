"""Truth-first pipeline view backend.

The Sprint 14 Day 2 regression: 96 SFN executions on 2026-04-22 reported
status=SUCCEEDED while their *output* contained `failure_reason: "stub
termination"` plus an ECS ExitCode=1. Dashboards reading status showed
green; the real state was total failure. This endpoint integrates
SFN output (not status), ECS exit codes, CFN first-failed-resource,
IAM AssumeRole events, and regional quota utilisation so callers can
classify each execution against ground truth.

GET /api/v2/pipeline-truth/executions
GET /api/v2/pipeline-truth/executions/{execution_arn:path}
GET /api/v2/pipeline-truth/quotas
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from nexus.aws_client import _client

logger = logging.getLogger("nexus.dashboard.pipeline_truth")

router = APIRouter(prefix="/api/v2/pipeline-truth", tags=["pipeline-truth"])

_DEFAULT_SM_ARN = (
    "arn:aws:states:us-east-1:418295677815:stateMachine:forgewing-deploy-v2"
)
ECS_CLUSTER = "aria-platform"

VERDICT_KINDS = (
    "GENUINE_SUCCESS",
    "STUB_TERMINATION",
    "CFN_FAILURE",
    "ECS_TASK_FAILURE",
    "GENUINE_FAILURE",
    "IN_PROGRESS",
    "UNKNOWN",
)


@dataclass
class ExecutionVerdict:
    kind: str
    reason: str
    signals: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "signals": self.signals}


def _parse_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _extract_ecs_exit_codes(sfn_output: dict[str, Any] | None) -> list[int]:
    """Pull ECS container ExitCodes out of a stub-termination output payload.

    The SFN output embeds `stack_error.Cause` as a JSON string whose decoded
    form is an ECS task description — that's where ExitCode lives.
    """
    if not sfn_output:
        return []
    stack_error = sfn_output.get("stack_error")
    if not isinstance(stack_error, dict):
        return []
    cause_raw = stack_error.get("Cause")
    cause = _parse_json(cause_raw) if isinstance(cause_raw, str) else cause_raw
    if not isinstance(cause, dict):
        return []
    codes: list[int] = []
    for container in cause.get("Containers") or []:
        if isinstance(container, dict) and "ExitCode" in container:
            try:
                codes.append(int(container["ExitCode"]))
            except (TypeError, ValueError):
                continue
    return codes


def categorise_execution(
    execution_arn: str,
    sfn_status: str,
    sfn_output: str | dict[str, Any] | None,
    ecs_task_exit_codes: list[int] | None = None,
    cfn_first_failed_resource: dict[str, Any] | None = None,
) -> ExecutionVerdict:
    """Truth-first classifier. Output is authoritative, status is a hint.

    The binary gate for the Day 2 regression: an execution whose
    sfn_output carries `failure_reason` containing "stub termination"
    MUST classify as STUB_TERMINATION regardless of status.
    """
    if sfn_status == "RUNNING":
        return ExecutionVerdict(
            kind="IN_PROGRESS",
            reason="SFN execution is still running",
            signals={"sfn_status": sfn_status},
        )

    output_obj = sfn_output if isinstance(sfn_output, dict) else _parse_json(sfn_output)
    exit_codes = ecs_task_exit_codes
    if exit_codes is None:
        exit_codes = _extract_ecs_exit_codes(output_obj)

    failure_reason = None
    recovered = None
    if isinstance(output_obj, dict):
        failure_reason = output_obj.get("failure_reason")
        recovered = output_obj.get("recovered")

    is_stub = (
        isinstance(failure_reason, str)
        and "stub" in failure_reason.lower()
        and "termin" in failure_reason.lower()
    )

    if is_stub:
        return ExecutionVerdict(
            kind="STUB_TERMINATION",
            reason=(
                f"SFN status={sfn_status} but output carries "
                f"failure_reason={failure_reason!r} — execution terminated "
                "without deploying"
            ),
            signals={
                "sfn_status": sfn_status,
                "failure_reason": failure_reason,
                "recovered": recovered,
                "ecs_exit_codes": exit_codes,
            },
        )

    if cfn_first_failed_resource:
        status = cfn_first_failed_resource.get("ResourceStatus", "")
        if "FAILED" in status or "ROLLBACK" in status:
            return ExecutionVerdict(
                kind="CFN_FAILURE",
                reason=(
                    f"CFN resource {cfn_first_failed_resource.get('LogicalResourceId')} "
                    f"in state {status}"
                ),
                signals={
                    "sfn_status": sfn_status,
                    "cfn_failed_resource": cfn_first_failed_resource,
                },
            )

    nonzero_exits = [c for c in exit_codes if c != 0]
    if nonzero_exits and sfn_status == "SUCCEEDED":
        return ExecutionVerdict(
            kind="ECS_TASK_FAILURE",
            reason=(
                f"SFN status=SUCCEEDED but {len(nonzero_exits)} ECS "
                f"container(s) exited with non-zero codes: {nonzero_exits}"
            ),
            signals={"sfn_status": sfn_status, "ecs_exit_codes": exit_codes},
        )

    if sfn_status == "SUCCEEDED":
        return ExecutionVerdict(
            kind="GENUINE_SUCCESS",
            reason="SFN succeeded and no failure signals detected in output",
            signals={
                "sfn_status": sfn_status,
                "ecs_exit_codes": exit_codes,
                "has_output": output_obj is not None,
            },
        )

    if sfn_status in ("FAILED", "TIMED_OUT", "ABORTED"):
        return ExecutionVerdict(
            kind="GENUINE_FAILURE",
            reason=f"SFN status={sfn_status}",
            signals={"sfn_status": sfn_status, "failure_reason": failure_reason},
        )

    return ExecutionVerdict(
        kind="UNKNOWN",
        reason=f"Could not classify: status={sfn_status}, output={'present' if output_obj else 'missing'}",
        signals={"sfn_status": sfn_status},
    )


def _to_iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        return dt.isoformat()
    if isinstance(dt, (int, float)):
        return datetime.fromtimestamp(dt, tz=timezone.utc).isoformat()
    return str(dt)


async def _describe_execution_async(arn: str) -> dict[str, Any]:
    return await asyncio.to_thread(
        _client("stepfunctions").describe_execution, executionArn=arn
    )


async def _find_cfn_failed_resource_async(stack_name: str) -> dict[str, Any] | None:
    """Return the first FAILED/ROLLBACK resource event for a stack, oldest first."""
    if not stack_name:
        return None

    def _call() -> dict[str, Any] | None:
        try:
            resp = _client("cloudformation").describe_stack_events(StackName=stack_name)
        except Exception:
            logger.debug("describe_stack_events(%s) failed", stack_name, exc_info=True)
            return None
        events = resp.get("StackEvents", []) or []
        # oldest first — earliest failure is the root cause
        for ev in reversed(events):
            status = ev.get("ResourceStatus", "")
            if "FAILED" in status or "ROLLBACK" in status:
                return {
                    "LogicalResourceId": ev.get("LogicalResourceId"),
                    "ResourceType": ev.get("ResourceType"),
                    "ResourceStatus": status,
                    "ResourceStatusReason": ev.get("ResourceStatusReason"),
                    "Timestamp": _to_iso(ev.get("Timestamp")),
                }
        return None

    return await asyncio.to_thread(_call)


async def _describe_ecs_tasks_async(task_arns: list[str]) -> list[dict[str, Any]]:
    if not task_arns:
        return []

    def _call() -> list[dict[str, Any]]:
        try:
            resp = _client("ecs").describe_tasks(cluster=ECS_CLUSTER, tasks=task_arns)
        except Exception:
            logger.debug("describe_tasks failed", exc_info=True)
            return []
        out = []
        for t in resp.get("tasks", []) or []:
            containers = t.get("containers", []) or []
            out.append({
                "task_arn": t.get("taskArn"),
                "last_status": t.get("lastStatus"),
                "stopped_reason": t.get("stoppedReason"),
                "exit_codes": [c.get("exitCode") for c in containers if "exitCode" in c],
            })
        return out

    return await asyncio.to_thread(_call)


async def _lookup_assume_role_events_async(
    start: datetime, end: datetime, role_arn: str | None
) -> list[dict[str, Any]]:
    if not role_arn:
        return []

    def _call() -> list[dict[str, Any]]:
        try:
            resp = _client("cloudtrail").lookup_events(
                LookupAttributes=[
                    {"AttributeKey": "EventName", "AttributeValue": "AssumeRole"}
                ],
                StartTime=start,
                EndTime=end,
                MaxResults=25,
            )
        except Exception:
            logger.debug("cloudtrail lookup_events failed", exc_info=True)
            return []
        out = []
        for ev in resp.get("Events", []) or []:
            out.append({
                "event_time": _to_iso(ev.get("EventTime")),
                "username": ev.get("Username"),
                "event_source": ev.get("EventSource"),
                "error_code": ev.get("ErrorCode"),
                "error_message": ev.get("ErrorMessage"),
            })
        return out

    return await asyncio.to_thread(_call)


async def _fetch_execution_evidence_async(arn: str) -> dict[str, Any]:
    """Gather all signals needed to categorise a single execution."""
    desc = await _describe_execution_async(arn)
    sfn_output = desc.get("output")
    sfn_input = desc.get("input")
    sfn_status = desc.get("status", "UNKNOWN")
    output_obj = _parse_json(sfn_output) if isinstance(sfn_output, str) else sfn_output
    stack_name = (output_obj or {}).get("stack_name") if isinstance(output_obj, dict) else None
    ecs_exit_codes = _extract_ecs_exit_codes(output_obj)

    cfn_task: Any = _find_cfn_failed_resource_async(stack_name) if stack_name else None
    tasks: list[Any] = [cfn_task] if cfn_task is not None else []
    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    cfn_failed = None
    if results:
        val = results[0]
        if not isinstance(val, BaseException):
            cfn_failed = val

    return {
        "sfn_status": sfn_status,
        "sfn_input": sfn_input,
        "sfn_output": sfn_output,
        "sfn_output_parsed": output_obj,
        "start_date": _to_iso(desc.get("startDate")),
        "stop_date": _to_iso(desc.get("stopDate")),
        "ecs_task_exit_codes": ecs_exit_codes,
        "cfn_first_failed_resource": cfn_failed,
        "stack_name": stack_name,
    }


def fetch_execution_evidence(arn: str) -> dict[str, Any]:
    """Sync wrapper around _fetch_execution_evidence_async for tests and scripts."""
    return asyncio.run(_fetch_execution_evidence_async(arn))


@router.get("/executions")
async def list_executions(
    state_machine_arn: str = Query(_DEFAULT_SM_ARN),
    status_filter: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
) -> dict[str, Any]:
    valid = {"RUNNING", "SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}
    if status_filter is not None and status_filter not in valid:
        raise HTTPException(400, f"status_filter must be one of {sorted(valid)}")

    sfn = _client("stepfunctions")
    params: dict[str, Any] = {"stateMachineArn": state_machine_arn, "maxResults": limit}
    if status_filter:
        params["statusFilter"] = status_filter

    try:
        resp = await asyncio.to_thread(sfn.list_executions, **params)
    except Exception as e:
        logger.warning("list_executions failed: %s", e)
        return {"state_machine_arn": state_machine_arn, "executions": [], "count": 0,
                "error": str(e)}

    arns = [e["executionArn"] for e in resp.get("executions", [])]
    evidence_coros = [_fetch_execution_evidence_async(a) for a in arns]
    evidence_list = await asyncio.gather(*evidence_coros, return_exceptions=True)

    out = []
    for arn, evidence in zip(arns, evidence_list):
        if isinstance(evidence, BaseException):
            logger.debug("evidence fetch failed for %s: %s", arn, evidence)
            out.append({"execution_arn": arn, "verdict": {
                "kind": "UNKNOWN", "reason": f"evidence fetch error: {evidence}",
                "signals": {}}})
            continue
        verdict = categorise_execution(
            execution_arn=arn,
            sfn_status=evidence["sfn_status"],
            sfn_output=evidence["sfn_output_parsed"],
            ecs_task_exit_codes=evidence["ecs_task_exit_codes"],
            cfn_first_failed_resource=evidence["cfn_first_failed_resource"],
        )
        out.append({
            "execution_arn": arn,
            "sfn_status": evidence["sfn_status"],
            "start_date": evidence["start_date"],
            "stop_date": evidence["stop_date"],
            "stack_name": evidence["stack_name"],
            "verdict": verdict.to_dict(),
        })

    return {"state_machine_arn": state_machine_arn, "executions": out, "count": len(out)}


@router.get("/executions/{execution_arn:path}")
async def execution_detail(execution_arn: str) -> dict[str, Any]:
    try:
        evidence = await _fetch_execution_evidence_async(execution_arn)
    except Exception as e:
        logger.warning("evidence fetch failed: %s", e)
        raise HTTPException(502, f"failed to fetch execution evidence: {e}") from e

    task_arns: list[str] = []
    parsed = evidence.get("sfn_output_parsed")
    if isinstance(parsed, dict):
        se = parsed.get("stack_error")
        if isinstance(se, dict):
            cause = se.get("Cause")
            cause_obj = _parse_json(cause) if isinstance(cause, str) else cause
            if isinstance(cause_obj, dict) and cause_obj.get("TaskArn"):
                task_arns.append(cause_obj["TaskArn"])

    start_dt = desc_start_dt(evidence["start_date"])
    stop_dt = desc_start_dt(evidence["stop_date"]) or datetime.now(timezone.utc)
    role_arn = None
    if isinstance(parsed, dict):
        for check in (parsed.get("checks") or []):
            if isinstance(check, dict) and check.get("name") == "aws_role_arn":
                detail = check.get("detail") or ""
                if "arn:aws:iam" in detail:
                    role_arn = detail.split("Role:", 1)[-1].strip() or None
                    break

    ecs_tasks_coro = _describe_ecs_tasks_async(task_arns)
    ct_coro = _lookup_assume_role_events_async(
        start_dt or datetime.now(timezone.utc), stop_dt, role_arn
    ) if role_arn and start_dt else asyncio.sleep(0, result=[])
    ecs_tasks, ct_events = await asyncio.gather(ecs_tasks_coro, ct_coro)

    verdict = categorise_execution(
        execution_arn=execution_arn,
        sfn_status=evidence["sfn_status"],
        sfn_output=evidence["sfn_output_parsed"],
        ecs_task_exit_codes=evidence["ecs_task_exit_codes"],
        cfn_first_failed_resource=evidence["cfn_first_failed_resource"],
    )

    return {
        "execution_arn": execution_arn,
        "sfn_status": evidence["sfn_status"],
        "sfn_input": evidence["sfn_input"],
        "sfn_output": evidence["sfn_output"],
        "start_date": evidence["start_date"],
        "stop_date": evidence["stop_date"],
        "stack_name": evidence["stack_name"],
        "ecs_tasks": ecs_tasks,
        "cfn_first_failed_resource": evidence["cfn_first_failed_resource"],
        "cloudtrail_assume_role_events": ct_events,
        "verdict": verdict.to_dict(),
    }


def desc_start_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


_QUOTA_CACHE: dict[str, Any] = {"expires_at": 0.0, "data": None}
_QUOTA_TTL_SECONDS = 300

_QUOTA_DEFS = [
    ("alb",             "elasticloadbalancing", "L-53DA6B97"),
    ("eip",             "ec2",                  "L-0263D0A3"),
    ("lambda_concurrent", "lambda",             "L-B99A9384"),
]


async def _fetch_quota(service_code: str, quota_code: str) -> dict[str, Any]:
    def _call() -> dict[str, Any]:
        try:
            resp = _client("service-quotas").get_service_quota(
                ServiceCode=service_code, QuotaCode=quota_code
            )
            q = resp.get("Quota", {})
            return {"limit": q.get("Value"), "unit": q.get("Unit"),
                    "adjustable": q.get("Adjustable")}
        except Exception as e:
            logger.debug("service-quotas %s/%s failed: %s", service_code, quota_code, e)
            return {"limit": None, "error": str(e)}

    return await asyncio.to_thread(_call)


@router.get("/quotas")
async def regional_quotas() -> dict[str, Any]:
    now = time.monotonic()
    if _QUOTA_CACHE["data"] is not None and now < _QUOTA_CACHE["expires_at"]:
        return {"cached": True, "quotas": _QUOTA_CACHE["data"]}

    results = await asyncio.gather(*[
        _fetch_quota(service_code, quota_code)
        for (_, service_code, quota_code) in _QUOTA_DEFS
    ])
    quotas = {name: data for (name, _, _), data in zip(_QUOTA_DEFS, results)}

    _QUOTA_CACHE["data"] = quotas
    _QUOTA_CACHE["expires_at"] = now + _QUOTA_TTL_SECONDS
    return {"cached": False, "quotas": quotas}

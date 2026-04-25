"""Tool 7 — list_aws_resources: catalog enumeration across AWS resource types.

Where read_aws_resource fetches one named resource, this tool answers
"how many X" / "list all X" by paginated boto3 list/describe calls.
Capped at 200 items per call.
"""
from __future__ import annotations

from typing import Any, Callable

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolUnknown, map_boto_error,
)


MAX_ITEMS = 200

VALID_RESOURCE_TYPES = [
    "ecs_clusters", "ecs_services", "lambda_functions",
    "rds_instances", "ec2_instances", "cfn_stacks",
    "s3_buckets", "step_function_executions",
    "secrets", "kms_keys",
]

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "resource_type": {"type": "string", "enum": VALID_RESOURCE_TYPES,
                          "description": "Catalog of AWS resource type to enumerate."},
        "filters": {"type": "object",
                    "description": "Optional resource-type-dependent filters "
                                   "(e.g., {cluster: 'overwatch-platform'})."},
    },
    "required": ["resource_type"],
}


def _client(service: str):
    from nexus.aws_client import _client as factory
    return factory(service)


def _cap(items: list) -> tuple[list, bool]:
    return items[:MAX_ITEMS], len(items) > MAX_ITEMS


def _list_ecs_clusters(_: dict) -> dict:
    arns: list[str] = []
    for page in _client("ecs").get_paginator("list_clusters").paginate():
        arns.extend(page.get("clusterArns") or [])
    capped, trunc = _cap(arns)
    return {"count": len(capped),
            "items": [{"arn": a, "name": a.rsplit("/", 1)[-1]} for a in capped],
            "truncated": trunc}


def _list_ecs_services(filters: dict) -> dict:
    cluster = filters.get("cluster") or "overwatch-platform"
    arns: list[str] = []
    for page in _client("ecs").get_paginator("list_services").paginate(cluster=cluster):
        arns.extend(page.get("serviceArns") or [])
    capped, trunc = _cap(arns)
    return {"count": len(capped), "cluster": cluster,
            "items": [{"arn": a, "name": a.rsplit("/", 1)[-1]} for a in capped],
            "truncated": trunc}


def _list_lambda_functions(_: dict) -> dict:
    fns: list[dict] = []
    for page in _client("lambda").get_paginator("list_functions").paginate():
        for f in page.get("Functions") or []:
            fns.append({"name": f.get("FunctionName"), "arn": f.get("FunctionArn"),
                        "runtime": f.get("Runtime"), "last_modified": f.get("LastModified")})
    capped, trunc = _cap(fns)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_rds_instances(_: dict) -> dict:
    items: list[dict] = []
    for page in _client("rds").get_paginator("describe_db_instances").paginate():
        for i in page.get("DBInstances") or []:
            items.append({"id": i.get("DBInstanceIdentifier"), "engine": i.get("Engine"),
                          "status": i.get("DBInstanceStatus"),
                          "endpoint": (i.get("Endpoint") or {}).get("Address"),
                          "arn": i.get("DBInstanceArn")})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_ec2_instances(filters: dict) -> dict:
    states = filters.get("states") or ["running", "pending", "stopping", "stopped"]
    items: list[dict] = []
    f = [{"Name": "instance-state-name", "Values": states}]
    for page in _client("ec2").get_paginator("describe_instances").paginate(Filters=f):
        for r in page.get("Reservations") or []:
            for inst in r.get("Instances") or []:
                items.append({"id": inst.get("InstanceId"),
                              "type": inst.get("InstanceType"),
                              "state": (inst.get("State") or {}).get("Name"),
                              "private_ip": inst.get("PrivateIpAddress")})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_cfn_stacks(filters: dict) -> dict:
    statuses = filters.get("statuses") or [
        "CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE",
        "CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"]
    items: list[dict] = []
    pgr = _client("cloudformation").get_paginator("list_stacks")
    for page in pgr.paginate(StackStatusFilter=statuses):
        for s in page.get("StackSummaries") or []:
            items.append({"name": s.get("StackName"), "status": s.get("StackStatus"),
                          "creation_time": str(s.get("CreationTime"))})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_s3_buckets(_: dict) -> dict:
    r = _client("s3").list_buckets()
    items = [{"name": b.get("Name"), "created": str(b.get("CreationDate"))}
             for b in (r.get("Buckets") or [])]
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_sfn_executions(filters: dict) -> dict:
    sm_arn = filters.get("state_machine_arn")
    if not sm_arn:
        raise ToolUnknown("step_function_executions requires filters.state_machine_arn")
    items: list[dict] = []
    for page in _client("stepfunctions").get_paginator("list_executions").paginate(stateMachineArn=sm_arn):
        for e in page.get("executions") or []:
            items.append({"arn": e.get("executionArn"), "name": e.get("name"),
                          "status": e.get("status"), "start": str(e.get("startDate"))})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_secrets(_: dict) -> dict:
    items: list[dict] = []
    for page in _client("secretsmanager").get_paginator("list_secrets").paginate():
        for s in page.get("SecretList") or []:
            items.append({"name": s.get("Name"), "arn": s.get("ARN"),
                          "last_changed": str(s.get("LastChangedDate") or "")})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


def _list_kms_keys(_: dict) -> dict:
    items: list[dict] = []
    for page in _client("kms").get_paginator("list_keys").paginate():
        for k in page.get("Keys") or []:
            items.append({"id": k.get("KeyId"), "arn": k.get("KeyArn")})
    capped, trunc = _cap(items)
    return {"count": len(capped), "items": capped, "truncated": trunc}


_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "ecs_clusters": _list_ecs_clusters, "ecs_services": _list_ecs_services,
    "lambda_functions": _list_lambda_functions, "rds_instances": _list_rds_instances,
    "ec2_instances": _list_ec2_instances, "cfn_stacks": _list_cfn_stacks,
    "s3_buckets": _list_s3_buckets, "step_function_executions": _list_sfn_executions,
    "secrets": _list_secrets, "kms_keys": _list_kms_keys,
}


def handler(**params: Any) -> dict:
    rt = params["resource_type"]
    fn = _HANDLERS.get(rt)
    if fn is None:
        raise ToolUnknown(f"unhandled resource_type: {rt!r}")
    try:
        return fn(params.get("filters") or {})
    except ToolUnknown:
        raise
    except Exception as e:
        raise map_boto_error(e) from e


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="list_aws_resources",
        description=(
            "Catalog-level enumeration of AWS resources. Use when the operator "
            "asks 'how many X' or 'list all X'. For a single resource by ID, use "
            "read_aws_resource. Capped at " + str(MAX_ITEMS) + " items per call."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

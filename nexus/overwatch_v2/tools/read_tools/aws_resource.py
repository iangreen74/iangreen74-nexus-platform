"""Tool 1 — read_aws_resource: dispatch reads across 10 AWS resource types."""
from __future__ import annotations

from typing import Any

from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolNotFound, ToolUnknown, map_boto_error,
)


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "resource_type": {
            "type": "string",
            "enum": ["cfn_stack", "ecs_task", "ecs_service", "iam_role",
                     "lambda_function", "sfn_execution", "ecr_repository",
                     "rds_instance", "kms_key", "secret"],
            "description": "AWS resource type to read.",
        },
        "identifier": {
            "type": "string",
            "description": "ARN, name, or ID depending on resource_type.",
        },
        "cluster": {
            "type": "string",
            "description": "ECS cluster (required when resource_type=ecs_task or ecs_service).",
        },
    },
    "required": ["resource_type", "identifier"],
}


def _client(service: str):
    from nexus.aws_client import _client as factory
    return factory(service)


def handler(**params: Any) -> dict:
    rtype = params["resource_type"]
    ident = params["identifier"]
    try:
        if rtype == "cfn_stack":
            return _read_cfn_stack(ident)
        if rtype == "ecs_task":
            return _read_ecs_task(ident, params.get("cluster", ""))
        if rtype == "ecs_service":
            return _read_ecs_service(ident, params.get("cluster", ""))
        if rtype == "iam_role":
            return _read_iam_role(ident)
        if rtype == "lambda_function":
            return _read_lambda(ident)
        if rtype == "sfn_execution":
            return _read_sfn_execution(ident)
        if rtype == "ecr_repository":
            return _read_ecr_repo(ident)
        if rtype == "rds_instance":
            return _read_rds_instance(ident)
        if rtype == "kms_key":
            return _read_kms_key(ident)
        if rtype == "secret":
            return _read_secret(ident)
        raise ToolUnknown(f"unhandled resource_type: {rtype!r}")
    except (ToolNotFound, ToolUnknown):
        raise
    except Exception as e:
        raise map_boto_error(e) from e


def _read_cfn_stack(name: str) -> dict:
    r = _client("cloudformation").describe_stacks(StackName=name)
    if not r.get("Stacks"):
        raise ToolNotFound(f"stack not found: {name}")
    s = r["Stacks"][0]
    return {
        "status": s["StackStatus"], "creation_time": str(s.get("CreationTime")),
        "outputs": {o["OutputKey"]: o["OutputValue"] for o in s.get("Outputs", [])},
        "parameters": {p["ParameterKey"]: p["ParameterValue"] for p in s.get("Parameters", [])},
    }


def _read_ecs_task(arn: str, cluster: str) -> dict:
    r = _client("ecs").describe_tasks(cluster=cluster, tasks=[arn])
    if not r.get("tasks"):
        raise ToolNotFound(f"ecs task not found: {arn}")
    t = r["tasks"][0]
    return {"last_status": t.get("lastStatus"), "desired_status": t.get("desiredStatus"),
            "stopped_reason": t.get("stoppedReason"), "task_definition": t.get("taskDefinitionArn")}


def _read_ecs_service(name: str, cluster: str) -> dict:
    r = _client("ecs").describe_services(cluster=cluster, services=[name])
    if not r.get("services"):
        raise ToolNotFound(f"ecs service not found: {name}")
    s = r["services"][0]
    return {"status": s.get("status"), "desired_count": s.get("desiredCount"),
            "running_count": s.get("runningCount"), "task_definition": s.get("taskDefinition")}


def _read_iam_role(name: str) -> dict:
    r = _client("iam").get_role(RoleName=name)["Role"]
    return {"arn": r["Arn"], "create_date": str(r["CreateDate"]),
            "assume_role_policy": r.get("AssumeRolePolicyDocument", {}),
            "description": r.get("Description")}


def _read_lambda(name: str) -> dict:
    r = _client("lambda").get_function(FunctionName=name)["Configuration"]
    return {"arn": r["FunctionArn"], "runtime": r.get("Runtime"),
            "last_modified": r.get("LastModified"), "state": r.get("State")}


def _read_sfn_execution(arn: str) -> dict:
    r = _client("stepfunctions").describe_execution(executionArn=arn)
    return {"status": r["status"], "start_date": str(r["startDate"]),
            "stop_date": str(r.get("stopDate") or ""), "output": r.get("output", "")}


def _read_ecr_repo(name: str) -> dict:
    r = _client("ecr").describe_repositories(repositoryNames=[name])["repositories"][0]
    return {"arn": r["repositoryArn"], "uri": r["repositoryUri"],
            "created_at": str(r.get("createdAt"))}


def _read_rds_instance(ident: str) -> dict:
    r = _client("rds").describe_db_instances(DBInstanceIdentifier=ident)["DBInstances"][0]
    return {"arn": r["DBInstanceArn"], "status": r.get("DBInstanceStatus"),
            "engine": r.get("Engine"), "endpoint": (r.get("Endpoint") or {}).get("Address")}


def _read_kms_key(ident: str) -> dict:
    r = _client("kms").describe_key(KeyId=ident)["KeyMetadata"]
    return {"arn": r["Arn"], "key_state": r.get("KeyState"),
            "key_usage": r.get("KeyUsage"), "key_spec": r.get("KeySpec")}


def _read_secret(ident: str) -> dict:
    r = _client("secretsmanager").describe_secret(SecretId=ident)
    return {"arn": r["ARN"], "name": r.get("Name"),
            "last_changed_date": str(r.get("LastChangedDate") or "")}


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_aws_resource",
        description="Read a single AWS resource's state (CFN stack, ECS task/service, IAM role, Lambda, SFN execution, ECR repo, RDS instance, KMS key, secret).",
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

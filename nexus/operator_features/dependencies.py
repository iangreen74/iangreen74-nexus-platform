"""Dependency walker for the Phase 0e.2 report engine.

Reads the OPERATOR_DEPENDS_ON edges of an OperatorFeature via the
0e.1 persistence layer, then for each edge target dispatches to the
appropriate AWS API check based on the target's Neptune label.

Supported labels: ``ECSService``, ``RDSInstance``, ``LambdaFunction``,
``S3Bucket``. Other labels (or future ones) return UNKNOWN with a
descriptive ``detail`` string. Per-handler exceptions are caught and
converted to UNKNOWN so a single failing dependency check does not
break the walk for the rest.

The default ECS cluster is ``overwatch-platform`` (NEXUS deploy
target). To target a different cluster, encode the resource id as
``<cluster>/<service>``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

import boto3

from nexus.operator_features import persistence
from nexus.operator_features.report import DependencyStatus
from nexus.operator_features.signals import SignalStatus

logger = logging.getLogger(__name__)

_DEFAULT_CLUSTER = "overwatch-platform"
_AWS_REGION = "us-east-1"


def walk_dependencies(
    feature_id: str,
    tenant_id: str = "_fleet",
) -> list[DependencyStatus]:
    """Evaluate every OPERATOR_DEPENDS_ON dependency of a feature.

    Returns one DependencyStatus per edge. Empty list if the feature
    does not exist or has no dependencies.
    """
    edges = persistence.walk_dependencies(feature_id, tenant_id=tenant_id)
    results: list[DependencyStatus] = []
    for edge in edges:
        to_label = edge.get("to_label", "")
        to_id = edge.get("to_id", "")
        results.append(_check_one(to_label, to_id))
    return results


def _check_one(to_label: str, to_id: str) -> DependencyStatus:
    """Dispatch to the per-label handler with universal error wrapping."""
    handler: Callable[[str], DependencyStatus] | None = _HANDLERS.get(to_label)
    if handler is None:
        return DependencyStatus(
            resource_type=to_label,
            resource_name=to_id,
            status=SignalStatus.UNKNOWN,
            detail=f"no handler for resource_type={to_label!r}",
        )
    try:
        return handler(to_id)
    except Exception as exc:  # noqa: BLE001 — convert any failure to UNKNOWN
        logger.warning(
            "dependency check failed: type=%s name=%s err=%s",
            to_label, to_id, exc,
        )
        return DependencyStatus(
            resource_type=to_label,
            resource_name=to_id,
            status=SignalStatus.UNKNOWN,
            detail=f"check failed: {type(exc).__name__}: {exc}",
        )


def _check_ecs_service(resource_id: str) -> DependencyStatus:
    cluster, service = _parse_ecs_id(resource_id)
    ecs = boto3.client("ecs", region_name=_AWS_REGION)
    resp = ecs.describe_services(cluster=cluster, services=[service])
    services = resp.get("services") or []
    if not services:
        return DependencyStatus(
            resource_type="ECSService", resource_name=resource_id,
            status=SignalStatus.RED, detail="service not found",
        )
    svc = services[0]
    desired = int(svc.get("desiredCount", 0))
    running = int(svc.get("runningCount", 0))
    svc_status = svc.get("status", "UNKNOWN")
    if desired > 0 and running == desired and svc_status == "ACTIVE":
        status = SignalStatus.GREEN
    elif running > 0:
        status = SignalStatus.AMBER
    else:
        status = SignalStatus.RED
    return DependencyStatus(
        resource_type="ECSService", resource_name=resource_id,
        status=status,
        detail=f"desired={desired} running={running} svc_status={svc_status}",
        raw={"desired": desired, "running": running,
             "service_status": svc_status, "cluster": cluster},
    )


def _check_rds_instance(resource_id: str) -> DependencyStatus:
    rds = boto3.client("rds", region_name=_AWS_REGION)
    resp = rds.describe_db_instances(DBInstanceIdentifier=resource_id)
    instances = resp.get("DBInstances") or []
    if not instances:
        return DependencyStatus(
            resource_type="RDSInstance", resource_name=resource_id,
            status=SignalStatus.RED, detail="instance not found",
        )
    inst = instances[0]
    db_status = inst.get("DBInstanceStatus", "unknown")
    health = (
        SignalStatus.GREEN if db_status == "available"
        else SignalStatus.AMBER
    )
    return DependencyStatus(
        resource_type="RDSInstance", resource_name=resource_id,
        status=health, detail=f"status={db_status}",
        raw={"db_status": db_status, "engine": inst.get("Engine"),
             "az": inst.get("AvailabilityZone")},
    )


def _check_lambda_function(resource_id: str) -> DependencyStatus:
    lam = boto3.client("lambda", region_name=_AWS_REGION)
    resp = lam.get_function_configuration(FunctionName=resource_id)
    state = resp.get("State", "unknown")
    if state == "Active":
        status = SignalStatus.GREEN
    elif state in ("Pending", "Inactive"):
        status = SignalStatus.AMBER
    else:
        status = SignalStatus.RED
    return DependencyStatus(
        resource_type="LambdaFunction", resource_name=resource_id,
        status=status, detail=f"state={state}",
        raw={"state": state, "runtime": resp.get("Runtime")},
    )


def _check_s3_bucket(resource_id: str) -> DependencyStatus:
    s3 = boto3.client("s3", region_name=_AWS_REGION)
    s3.head_bucket(Bucket=resource_id)
    return DependencyStatus(
        resource_type="S3Bucket", resource_name=resource_id,
        status=SignalStatus.GREEN, detail="reachable",
    )


def _parse_ecs_id(resource_id: str) -> tuple[str, str]:
    """``cluster/service`` or bare service (defaults to overwatch-platform)."""
    if "/" in resource_id:
        cluster, service = resource_id.split("/", 1)
        return cluster, service
    return _DEFAULT_CLUSTER, resource_id


_HANDLERS: dict[str, Callable[[str], DependencyStatus]] = {
    "ECSService": _check_ecs_service,
    "RDSInstance": _check_rds_instance,
    "LambdaFunction": _check_lambda_function,
    "S3Bucket": _check_s3_bucket,
}


__all__ = ["walk_dependencies"]

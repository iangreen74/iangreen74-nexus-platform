#!/usr/bin/env python3
"""One-shot AWS-state ingestion into the V2 engineering ontology.

Walks boto3 list/describe APIs across the Forgewing/Overwatch surface and
writes Service / Database / DataStore / Infrastructure / WorkerNode /
Deployment objects via Track E's propose_object()/update_object().

Operator-driven: not scheduled. Run via a one-off ECS task in the
overwatch-platform cluster (which has the V2 reasoner role and VPC
reach to RDS + Neptune):

    aws ecs run-task --cluster overwatch-platform \
        --task-definition aria-console:<latest> --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[...],securityGroups=[...]}" \
        --overrides '{"containerOverrides":[{"name":"aria-console","command":["python3","/app/scripts/ingest_aws_state.py"]}]}'

Idempotent: re-running updates objects keyed by (object_type, name).
Partial-failure tolerant: one boto3 call failing is logged but does not
stop the rest of the walk.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any, Callable

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest_aws_state")

REGION = os.environ.get("AWS_REGION", "us-east-1")


def _boto(service: str):
    import boto3
    return boto3.client(service, region_name=REGION)


# Existing-object lookup keyed by (object_type, name). Idempotency happens
# at the service layer; this function decides propose vs update.
def _upsert(object_type: str, name: str, properties: dict) -> str:
    from nexus.overwatch_v2.ontology import (
        list_objects_by_type, propose_object, update_object,
    )
    properties = {**properties, "name": name}
    rows = list_objects_by_type(object_type, limit=1000) or []
    existing = next((r for r in rows if r.get("name") == name), None)
    if existing:
        update_object(existing["id"], properties, actor="ingest_aws_state")
        return "update"
    propose_object(object_type, properties, actor="ingest_aws_state")
    return "propose"


def _safe(label: str, fn: Callable[[], int]) -> int:
    """Run a walker; never let one boto3 failure halt the ingestion."""
    try:
        n = fn()
        log.info("%s: %d objects", label, n)
        return n
    except Exception:
        log.exception("%s: walker failed; continuing", label)
        return 0


# --- walkers --------------------------------------------------------------

def walk_ecs_clusters() -> int:
    c = _boto("ecs")
    arns: list[str] = []
    for p in c.get_paginator("list_clusters").paginate():
        arns.extend(p.get("clusterArns") or [])
    for arn in arns:
        name = arn.rsplit("/", 1)[-1]
        _upsert("Service", name, {"kind": "ecs_cluster", "arn": arn,
                                  "region": REGION, "status": "ACTIVE"})
    return len(arns)


def walk_ecs_services() -> int:
    c = _boto("ecs")
    total = 0
    cluster_arns: list[str] = []
    for p in c.get_paginator("list_clusters").paginate():
        cluster_arns.extend(p.get("clusterArns") or [])
    for cluster in cluster_arns:
        cname = cluster.rsplit("/", 1)[-1]
        for p in c.get_paginator("list_services").paginate(cluster=cluster):
            for arn in p.get("serviceArns") or []:
                name = arn.rsplit("/", 1)[-1]
                _upsert("Service", f"{cname}/{name}",
                        {"kind": "ecs_service", "arn": arn, "region": REGION,
                         "status": "ACTIVE"})
                total += 1
    return total


def walk_lambda_functions() -> int:
    c = _boto("lambda")
    n = 0
    for p in c.get_paginator("list_functions").paginate():
        for f in p.get("Functions") or []:
            _upsert("Service", f.get("FunctionName") or "?", {
                "kind": "lambda", "arn": f.get("FunctionArn"),
                "region": REGION, "status": "ACTIVE",
            })
            n += 1
    return n


def walk_rds_instances() -> int:
    c = _boto("rds")
    n = 0
    for p in c.get_paginator("describe_db_instances").paginate():
        for i in p.get("DBInstances") or []:
            _upsert("Database", i.get("DBInstanceIdentifier") or "?", {
                "engine": i.get("Engine"),
                "instance_class": i.get("DBInstanceClass"),
                "status": i.get("DBInstanceStatus"),
                "endpoint": (i.get("Endpoint") or {}).get("Address"),
                "region": REGION, "arn": i.get("DBInstanceArn"),
            })
            n += 1
    return n


def walk_s3_buckets() -> int:
    r = _boto("s3").list_buckets()
    n = 0
    for b in r.get("Buckets") or []:
        _upsert("DataStore", b.get("Name") or "?", {
            "kind": "s3", "region": REGION,
            "arn": f"arn:aws:s3:::{b.get('Name')}",
        })
        n += 1
    return n


def walk_neptune_graphs() -> int:
    c = _boto("neptune-graph")
    n = 0
    for p in c.get_paginator("list_graphs").paginate():
        for g in p.get("graphs") or []:
            _upsert("DataStore", g.get("name") or g.get("id") or "?", {
                "kind": "neptune-graph", "status": g.get("status"),
                "arn": g.get("arn"), "region": REGION,
                "extra": {"graph_id": g.get("id")},
            })
            n += 1
    return n


def walk_vpcs_and_albs() -> int:
    n = 0
    for v in (_boto("ec2").describe_vpcs().get("Vpcs") or []):
        name = next((t["Value"] for t in (v.get("Tags") or [])
                     if t.get("Key") == "Name"), v.get("VpcId"))
        _upsert("Infrastructure", name, {"kind": "vpc",
                "arn": f"arn:aws:ec2:{REGION}::vpc/{v.get('VpcId')}",
                "region": REGION, "status": v.get("State")})
        n += 1
    for lb in (_boto("elbv2").describe_load_balancers().get("LoadBalancers") or []):
        _upsert("Infrastructure", lb.get("LoadBalancerName") or "?", {
            "kind": lb.get("Type") or "alb",
            "arn": lb.get("LoadBalancerArn"),
            "region": REGION, "status": (lb.get("State") or {}).get("Code"),
        })
        n += 1
    return n


def walk_ec2_instances() -> int:
    f = [{"Name": "instance-state-name",
          "Values": ["running", "pending", "stopping", "stopped"]}]
    n = 0
    for p in _boto("ec2").get_paginator("describe_instances").paginate(Filters=f):
        for r in p.get("Reservations") or []:
            for inst in r.get("Instances") or []:
                iid = inst.get("InstanceId") or "?"
                _upsert("WorkerNode", iid, {
                    "instance_id": iid,
                    "instance_type": inst.get("InstanceType"),
                    "ami_id": inst.get("ImageId"),
                    "status": (inst.get("State") or {}).get("Name"),
                    "region": REGION,
                    "private_ip": inst.get("PrivateIpAddress"),
                })
                n += 1
    return n


def walk_cfn_stacks() -> int:
    statuses = ["CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE",
                "CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"]
    n = 0
    for p in _boto("cloudformation").get_paginator(
            "list_stacks").paginate(StackStatusFilter=statuses):
        for s in p.get("StackSummaries") or []:
            name = s.get("StackName") or "?"
            _upsert("Deployment", name, {
                "service_name": name,
                "status": s.get("StackStatus"),
                "deployed_at": str(s.get("CreationTime") or ""),
                "region": REGION,
            })
            n += 1
    return n


def main() -> int:
    log.info("AWS state ingestion starting (region=%s)", REGION)
    started = time.time()
    counts = {
        "ecs_clusters": _safe("ecs_clusters", walk_ecs_clusters),
        "ecs_services": _safe("ecs_services", walk_ecs_services),
        "lambda_functions": _safe("lambda_functions", walk_lambda_functions),
        "rds_instances": _safe("rds_instances", walk_rds_instances),
        "s3_buckets": _safe("s3_buckets", walk_s3_buckets),
        "neptune_graphs": _safe("neptune_graphs", walk_neptune_graphs),
        "vpcs_and_albs": _safe("vpcs_and_albs", walk_vpcs_and_albs),
        "ec2_instances": _safe("ec2_instances", walk_ec2_instances),
        "cfn_stacks": _safe("cfn_stacks", walk_cfn_stacks),
    }
    elapsed = time.time() - started
    total = sum(counts.values())
    log.info("Ingestion complete. Total=%d in %.1fs. By type: %s",
             total, elapsed, counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())

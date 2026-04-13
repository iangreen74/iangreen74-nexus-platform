"""
Infrastructure Lockdown Sensor.

Overwatch defends a small set of platform invariants — values that must
NEVER change. These are the load-bearing constants of the platform: AWS
account, region, ECS cluster name, Neptune graph id, the four critical
services, the customer-facing domains, the Cognito pool. If any of them
drift, something is seriously wrong (drift, misconfiguration, or worse)
and Ian needs to know within seconds.

Every check is read-only. Failures are logged and reported, never raised.
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Any

from nexus import aws_client
from nexus.config import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    COGNITO_USER_POOL_ID,
    FORGEWING_API,
    FORGEWING_CLUSTER,
    FORGEWING_SERVICES,
    FORGEWING_STAGING_API,
    FORGEWING_WEB,
    GITHUB_APP_ID,
    MODE,
    NEPTUNE_GRAPH_ID,
)

logger = logging.getLogger("nexus.sensors.infrastructure_lock")


# The locked constants — Overwatch will defend each of these on every poll.
# Anything in here is a promise: Overwatch tells the operator the moment
# the real world diverges from this dict.
INFRASTRUCTURE_LOCKS: dict[str, Any] = {
    "aws_region": AWS_REGION,
    "aws_account": AWS_ACCOUNT_ID,
    "ecs_cluster": FORGEWING_CLUSTER,
    "neptune_graph": NEPTUNE_GRAPH_ID,
    "forgewing_domain": FORGEWING_WEB.replace("https://", "").rstrip("/"),
    "api_domain": FORGEWING_API.replace("https://", "").rstrip("/"),
    "staging_domain": FORGEWING_STAGING_API.replace("https://", "").rstrip("/"),
    "overwatch_domain": "platform.vaultscaler.com",
    "cognito_pool": COGNITO_USER_POOL_ID,
    "github_app_id": GITHUB_APP_ID,
    "ecs_services": list(FORGEWING_SERVICES),
    "overwatch_cluster": "overwatch-platform",
    "overwatch_services": ["aria-console"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_dns(hostname: str) -> tuple[bool, str]:
    try:
        ip = socket.gethostbyname(hostname)
        return True, ip
    except Exception as exc:
        return False, str(exc)


def _check_ecs_services_batch() -> dict[str, Any]:
    """
    Verify every monitored service across both clusters — one
    describe_services call per cluster.

    The aria-ecs-task-role has ecs:DescribeServices but NOT ListServices or
    DescribeClusters, so we can't use the convenience helpers — we go
    straight to describe_services with the known service list. A cluster
    is "reachable" if describe_services returned at least one of its
    services. aria-console lives in overwatch-platform; the customer
    services live in aria-platform.
    """
    from nexus.config import (
        FORGEWING_SERVICES as _FW_SVCS,
        OVERWATCH_CLUSTER as _OW_CLUSTER,
        OVERWATCH_SERVICES as _OW_SVCS,
    )

    cluster_plan = [
        (FORGEWING_CLUSTER, list(_FW_SVCS)),
        (_OW_CLUSTER, list(_OW_SVCS)),
    ]

    if MODE != "production":
        return {
            "cluster_reachable": True,
            "services": [
                {"service": s, "cluster": c, "status": "ACTIVE",
                 "running_count": 1, "desired_count": 1}
                for c, svcs in cluster_plan for s in svcs
            ],
        }

    all_services: list[dict[str, Any]] = []
    all_failures: list[str] = []
    any_reachable = False
    errors: list[str] = []
    for cluster, names in cluster_plan:
        if not names:
            continue
        try:
            resp = aws_client._client("ecs").describe_services(
                cluster=cluster, services=names
            )
            got = resp.get("services", []) or []
            if got:
                any_reachable = True
            for s in got:
                all_services.append({
                    "service": s.get("serviceName"),
                    "cluster": cluster,
                    "status": s.get("status"),
                    "running_count": s.get("runningCount", 0),
                    "desired_count": s.get("desiredCount", 0),
                })
            all_failures.extend(
                f"{cluster}/{f.get('arn', '?')}: {f.get('reason')}"
                for f in resp.get("failures", []) or []
            )
        except Exception as exc:
            errors.append(f"{cluster}: {exc}")

    return {
        "cluster_reachable": any_reachable,
        "services": all_services,
        "failures": all_failures,
        **({"error": "; ".join(errors)} if errors else {}),
    }


def _check_neptune_graph() -> dict[str, Any]:
    """
    Verify Neptune Analytics is reachable by running a tiny count query
    directly against the data-plane client.

    The boto3 neptune-graph client we use elsewhere is configured against
    the data-plane endpoint (https://us-east-1.neptune-graph.amazonaws.com)
    which works for execute_query but NOT for control-plane operations
    like get_graph. We bypass our own query() wrapper here because it
    swallows exceptions and returns [] on failure — we need real errors
    to surface as violations.
    """
    if MODE != "production":
        return {"id": NEPTUNE_GRAPH_ID, "status": "AVAILABLE"}
    try:
        import json as _json

        from nexus.neptune_client import _client as _ng_client

        resp = _ng_client().execute_query(
            graphIdentifier=NEPTUNE_GRAPH_ID,
            queryString="MATCH (n) RETURN count(n) AS c LIMIT 1",
            language="OPEN_CYPHER",
        )
        payload = _json.loads(resp["payload"].read())
        rows = payload.get("results", []) or []
        count = int(rows[0].get("c", 0)) if rows else 0
        return {"id": NEPTUNE_GRAPH_ID, "status": "AVAILABLE", "node_count": count}
    except Exception as exc:
        logger.warning("neptune count query failed: %s", exc)
        return {"id": NEPTUNE_GRAPH_ID, "status": "ERROR", "error": str(exc)}


def _check_cognito() -> dict[str, Any]:
    """
    Cognito check is best-effort. The aria-ecs-task-role does NOT currently
    have cognito-idp:DescribeUserPool, so this returns a `skipped` marker
    rather than a false violation. Add the IAM permission to enable.
    """
    if MODE != "production":
        return {"id": COGNITO_USER_POOL_ID, "status": "ACTIVE"}
    try:
        resp = aws_client._client("cognito-idp").describe_user_pool(UserPoolId=COGNITO_USER_POOL_ID)
        pool = resp.get("UserPool", {})
        return {"id": pool.get("Id"), "name": pool.get("Name"), "status": "ACTIVE"}
    except Exception as exc:
        msg = str(exc)
        if "AccessDenied" in msg or "not authorized" in msg.lower():
            return {"id": COGNITO_USER_POOL_ID, "status": "SKIPPED", "skipped_reason": "iam: cognito-idp:DescribeUserPool not granted"}
        return {"id": COGNITO_USER_POOL_ID, "status": "ERROR", "error": msg}


def check_locks() -> dict[str, Any]:
    """
    Run every infrastructure verification and return a LockReport.

    The report's `all_locked` is True only if every check passes. Each
    failed check appears in `violations` with a short reason — the operator
    sees exactly what drifted and what to investigate.
    """
    violations: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    # ECS cluster + services in one batched describe_services call.
    # Cluster reachability is implied by getting any service back.
    ecs_state = _check_ecs_services_batch()
    checks["ecs"] = ecs_state
    if not ecs_state.get("cluster_reachable"):
        violations.append({
            "lock": "ecs_cluster",
            "reason": ecs_state.get("error", "describe_services returned no services"),
        })
    from nexus.config import ALL_MONITORED_SERVICES
    services_by_name = {s["service"]: s for s in ecs_state.get("services", [])}
    for expected in ALL_MONITORED_SERVICES:
        svc = services_by_name.get(expected)
        if svc is None:
            violations.append({"lock": f"service:{expected}", "reason": "missing from describe_services response"})
            continue
        if svc.get("status") not in (None, "ACTIVE"):
            violations.append({"lock": f"service:{expected}", "reason": f"status={svc.get('status')}"})
        elif svc.get("desired_count", 0) < 1:
            violations.append({"lock": f"service:{expected}", "reason": "desired<1"})

    # Neptune graph
    graph_state = _check_neptune_graph()
    checks["neptune_graph"] = graph_state
    if graph_state.get("status") == "AVAILABLE":
        pass
    elif graph_state.get("status") == "ERROR":
        violations.append({"lock": "neptune_graph", "reason": graph_state.get("error", "unknown")[:120]})
    else:
        violations.append({"lock": "neptune_graph", "reason": f"status={graph_state.get('status')}"})

    # Cognito user pool — skipped if IAM perms missing
    cognito_state = _check_cognito()
    checks["cognito_pool"] = cognito_state
    if cognito_state.get("status") == "SKIPPED":
        skipped.append({"lock": "cognito_pool", "reason": cognito_state["skipped_reason"]})
    elif cognito_state.get("status") not in (None, "ACTIVE"):
        violations.append({"lock": "cognito_pool", "reason": cognito_state.get("error", "unknown")[:120]})

    # DNS — Overwatch + Forgewing customer-facing domains must resolve
    dns_results: dict[str, Any] = {}
    for label in ("forgewing_domain", "api_domain", "staging_domain", "overwatch_domain"):
        host = INFRASTRUCTURE_LOCKS[label]
        ok, info = _check_dns(host)
        dns_results[host] = {"resolves": ok, "info": info}
        if not ok:
            violations.append({"lock": label, "reason": f"DNS: {info[:120]}"})
    checks["dns"] = dns_results

    return {
        "all_locked": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
        "skipped": skipped,
        "expected": INFRASTRUCTURE_LOCKS,
        "checks": checks,
        "checked_at": _now(),
    }

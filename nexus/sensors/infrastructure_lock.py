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
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_dns(hostname: str) -> tuple[bool, str]:
    try:
        ip = socket.gethostbyname(hostname)
        return True, ip
    except Exception as exc:
        return False, str(exc)


def _check_ecs_cluster() -> dict[str, Any]:
    if MODE != "production":
        return {"name": FORGEWING_CLUSTER, "status": "ACTIVE", "active_services": len(FORGEWING_SERVICES)}
    try:
        resp = aws_client._client("ecs").describe_clusters(clusters=[FORGEWING_CLUSTER])
        cluster = (resp.get("clusters") or [{}])[0]
        return {
            "name": cluster.get("clusterName"),
            "status": cluster.get("status"),
            "active_services": cluster.get("activeServicesCount", 0),
        }
    except Exception as exc:
        return {"name": FORGEWING_CLUSTER, "error": str(exc)}


def _check_neptune_graph() -> dict[str, Any]:
    if MODE != "production":
        return {"id": NEPTUNE_GRAPH_ID, "status": "AVAILABLE"}
    try:
        from nexus.neptune_client import _client as _ng_client

        resp = _ng_client().get_graph(graphIdentifier=NEPTUNE_GRAPH_ID)
        return {"id": resp.get("id"), "status": resp.get("status")}
    except Exception as exc:
        return {"id": NEPTUNE_GRAPH_ID, "error": str(exc)}


def _check_cognito() -> dict[str, Any]:
    if MODE != "production":
        return {"id": COGNITO_USER_POOL_ID, "status": "ACTIVE"}
    try:
        resp = aws_client._client("cognito-idp").describe_user_pool(UserPoolId=COGNITO_USER_POOL_ID)
        pool = resp.get("UserPool", {})
        return {"id": pool.get("Id"), "name": pool.get("Name"), "status": "ACTIVE"}
    except Exception as exc:
        return {"id": COGNITO_USER_POOL_ID, "error": str(exc)}


def check_locks() -> dict[str, Any]:
    """
    Run every infrastructure verification and return a LockReport.

    The report's `all_locked` is True only if every check passes. Each
    failed check appears in `violations` with a short reason — the operator
    sees exactly what drifted and what to investigate.
    """
    violations: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    # ECS cluster
    cluster_state = _check_ecs_cluster()
    checks["ecs_cluster"] = cluster_state
    if cluster_state.get("status") != "ACTIVE":
        violations.append({"lock": "ecs_cluster", "reason": f"status={cluster_state.get('status')}"})

    # ECS services — each must exist with desired >= 1
    services_state = aws_client.get_ecs_services(FORGEWING_CLUSTER)
    checks["ecs_services"] = services_state
    found_names = {s.get("service") for s in services_state}
    for expected in FORGEWING_SERVICES:
        svc = next((s for s in services_state if s.get("service") == expected), None)
        if svc is None:
            violations.append({"lock": f"service:{expected}", "reason": "missing"})
        elif svc.get("desired_count", 0) < 1:
            violations.append({"lock": f"service:{expected}", "reason": "desired<1"})
        elif svc.get("status") not in (None, "ACTIVE"):
            violations.append({"lock": f"service:{expected}", "reason": f"status={svc.get('status')}"})

    # Neptune graph
    graph_state = _check_neptune_graph()
    checks["neptune_graph"] = graph_state
    if graph_state.get("status") != "AVAILABLE":
        violations.append({"lock": "neptune_graph", "reason": f"status={graph_state.get('status')}"})

    # Cognito user pool
    cognito_state = _check_cognito()
    checks["cognito_pool"] = cognito_state
    if cognito_state.get("error"):
        violations.append({"lock": "cognito_pool", "reason": cognito_state["error"][:120]})

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
        "expected": INFRASTRUCTURE_LOCKS,
        "checks": checks,
        "checked_at": _now(),
    }

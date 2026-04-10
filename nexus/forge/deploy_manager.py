"""
Deploy Manager — Overwatch's interface to aria-platform deployments.

Responsibilities:
- Trigger ECS rolling deploys (force-new-deployment)
- Trigger GitHub Actions workflow runs (workflow_dispatch)
- Roll back to a previous task definition revision
- Wait for an in-flight deployment to stabilize
- Report current deployment status

Every action records a HealingAction node via overwatch_graph so the
operator can audit what Overwatch has touched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus.aws_client import _client as _aws_client
from nexus.config import (
    ARIA_PLATFORM_DEFAULT_BRANCH,
    ARIA_PLATFORM_REPO,
    FORGEWING_CLUSTER,
    MODE,
)
from nexus.forge.aria_repo import _request as _gh_request

logger = logging.getLogger("nexus.forge.deploy_manager")


def _record(action: str, target: str, outcome: str, blast: str = "moderate", details: str = "") -> None:
    """Best-effort write to overwatch_graph."""
    try:
        from nexus import overwatch_graph

        overwatch_graph.record_healing_action(
            action_type=action,
            target=target,
            blast_radius=blast,
            trigger=details or "deploy_manager",
            outcome=outcome,
        )
    except Exception:
        logger.debug("graph record failed for %s/%s", action, target, exc_info=True)


def deploy_service(service_name: str, cluster: str = FORGEWING_CLUSTER) -> dict[str, Any]:
    """
    Force a new ECS deployment for the named service. Returns the
    initial deployment state. This is moderate blast radius — reversible
    by rollback_service if the new task fails health checks.
    """
    if MODE != "production":
        result = {"service": service_name, "cluster": cluster, "status": "PRIMARY", "mock": True}
        _record("deploy_service", service_name, "success", "moderate", "local mock")
        return result
    try:
        resp = _aws_client("ecs").update_service(
            cluster=cluster, service=service_name, forceNewDeployment=True
        )
        deployment = (resp.get("service", {}).get("deployments") or [{}])[0]
        outcome = "success" if deployment.get("status") == "PRIMARY" else "pending"
        _record("deploy_service", service_name, outcome, "moderate")
        return {
            "service": service_name,
            "cluster": cluster,
            "status": deployment.get("status"),
            "deployment_id": deployment.get("id"),
            "task_definition": deployment.get("taskDefinition"),
        }
    except Exception as exc:
        logger.exception("deploy_service failed for %s", service_name)
        _record("deploy_service", service_name, "failed", "moderate", str(exc))
        return {"service": service_name, "error": str(exc)}


def deploy_via_ci(workflow_name: str, ref: str = ARIA_PLATFORM_DEFAULT_BRANCH) -> dict[str, Any]:
    """
    Trigger a GitHub Actions workflow_dispatch for an aria-platform workflow.
    Returns {triggered: bool, workflow, ref}.
    """
    if MODE != "production":
        _record("deploy_via_ci", workflow_name, "success", "moderate", "local mock")
        return {"triggered": True, "workflow": workflow_name, "ref": ref, "mock": True}
    resp = _gh_request(
        "POST",
        f"/repos/{ARIA_PLATFORM_REPO}/actions/workflows/{workflow_name}/dispatches",
        json={"ref": ref},
    )
    triggered = resp is not None and resp.status_code == 204
    outcome = "success" if triggered else "failed"
    _record("deploy_via_ci", workflow_name, outcome, "moderate")
    return {
        "triggered": triggered,
        "workflow": workflow_name,
        "ref": ref,
        "status": getattr(resp, "status_code", None),
    }


def rollback_service(service_name: str, cluster: str = FORGEWING_CLUSTER) -> dict[str, Any]:
    """
    Roll the service back to its previous task definition revision.
    Discovers the prior revision from the family list — does NOT trust
    a cached value, in case Overwatch has been off-line.
    """
    if MODE != "production":
        _record("rollback_service", service_name, "success", "dangerous", "local mock")
        return {"service": service_name, "rolled_back_to": "previous", "mock": True}
    try:
        ecs = _aws_client("ecs")
        svc = ecs.describe_services(cluster=cluster, services=[service_name])
        current_td_arn = (svc["services"][0] or {}).get("taskDefinition", "")
        family = current_td_arn.split("/")[-1].rsplit(":", 1)[0]
        revs = ecs.list_task_definitions(familyPrefix=family, status="ACTIVE", sort="DESC", maxResults=5)
        arns = revs.get("taskDefinitionArns", [])
        if len(arns) < 2:
            _record("rollback_service", service_name, "failed", "dangerous", "no prior revision")
            return {"service": service_name, "error": "no prior revision available"}
        prior = arns[1]  # arns[0] is current
        ecs.update_service(cluster=cluster, service=service_name, taskDefinition=prior, forceNewDeployment=True)
        _record("rollback_service", service_name, "success", "dangerous", f"-> {prior}")
        return {"service": service_name, "rolled_back_to": prior}
    except Exception as exc:
        logger.exception("rollback_service failed for %s", service_name)
        _record("rollback_service", service_name, "failed", "dangerous", str(exc))
        return {"service": service_name, "error": str(exc)}


def get_deploy_status(service_name: str, cluster: str = FORGEWING_CLUSTER) -> dict[str, Any]:
    """Return the current PRIMARY deployment state for a service."""
    if MODE != "production":
        return {
            "service": service_name,
            "cluster": cluster,
            "status": "PRIMARY",
            "running": 1,
            "desired": 1,
            "rollout_state": "COMPLETED",
        }
    try:
        resp = _aws_client("ecs").describe_services(cluster=cluster, services=[service_name])
        svc = (resp.get("services") or [{}])[0]
        primary = next((d for d in (svc.get("deployments") or []) if d.get("status") == "PRIMARY"), {})
        return {
            "service": service_name,
            "cluster": cluster,
            "status": primary.get("status"),
            "running": primary.get("runningCount", 0),
            "desired": primary.get("desiredCount", 0),
            "failed": primary.get("failedTasks", 0),
            "rollout_state": primary.get("rolloutState"),
            "task_definition": primary.get("taskDefinition"),
        }
    except Exception:
        logger.exception("get_deploy_status failed for %s", service_name)
        return {"service": service_name, "error": True}


def wait_for_stable(service_name: str, cluster: str = FORGEWING_CLUSTER, timeout: int = 300) -> dict[str, Any]:
    """Block until the service stabilizes or `timeout` seconds elapse."""
    if MODE != "production":
        return {"service": service_name, "stable": True, "mock": True}
    started = datetime.now(timezone.utc)
    try:
        waiter = _aws_client("ecs").get_waiter("services_stable")
        waiter.wait(
            cluster=cluster,
            services=[service_name],
            WaiterConfig={"Delay": 15, "MaxAttempts": max(1, timeout // 15)},
        )
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        return {"service": service_name, "stable": True, "elapsed_seconds": int(elapsed)}
    except Exception as exc:
        logger.warning("wait_for_stable timed out for %s: %s", service_name, exc)
        return {"service": service_name, "stable": False, "error": str(exc)}

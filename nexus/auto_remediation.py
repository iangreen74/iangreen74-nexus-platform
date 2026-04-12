"""
Auto-Remediation — Overwatch fixes common issues without human intervention.

When synthetic tests detect a known failure, auto-remediation attempts
a fix before escalating. All remediations are Tier 2 (moderate blast
radius) — no dangerous actions are taken automatically.

Remediations:
1. API down → restart forgescaler ECS service
2. Missing brief → trigger brief regeneration via API
3. Deploy stuck → trigger readiness check via existing capability
4. Project separation broken → escalate (requires code fix)
5. Conversation down → restart forgescaler service
"""
from __future__ import annotations

import logging
from typing import Any

from nexus import overwatch_graph
from nexus.capabilities.forgewing_api import call_api
from nexus.config import BLAST_MODERATE, FORGEWING_CLUSTER, MODE

logger = logging.getLogger(__name__)


def remediate(failure: dict[str, Any]) -> dict[str, Any]:
    """Attempt to fix a detected failure.

    Returns {fixed: bool, action: str, detail: str}.
    """
    name = failure.get("name", "")

    handlers = {
        "health": _remediate_api_down,
        "brief_exists": _remediate_missing_brief,
        "deploy_readiness": _remediate_deploy,
        "conversation_scoping": _remediate_api_down,
        "sfs_detection": _remediate_api_down,
    }

    handler = handlers.get(name)
    if not handler:
        return {"fixed": False, "action": "none",
                "detail": f"No auto-remediation for '{name}' — escalate"}

    try:
        result = handler(failure)
        _record(name, result)
        return result
    except Exception as exc:
        logger.warning("Remediation for %s failed: %s", name, exc)
        return {"fixed": False, "action": "error", "detail": str(exc)[:200]}


def run_and_remediate() -> dict[str, Any]:
    """Run synthetic tests and attempt remediation on failures.

    Called by the execution loop every N cycles.
    """
    from nexus.synthetic_tests import run_all_journeys

    results = run_all_journeys(force=True)
    failures = [r for r in results if r["status"] == "fail"]
    remediations: list[dict[str, Any]] = []

    for failure in failures:
        rem = remediate(failure)
        remediations.append({"journey": failure["name"], **rem})
        level = "info" if rem["fixed"] else "warning"
        getattr(logger, level)(
            "Remediation %s: %s → %s", failure["name"], rem["action"], rem["detail"]
        )

    passed = sum(1 for r in results if r["status"] == "pass")
    fixed = sum(1 for r in remediations if r["fixed"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(failures),
        "remediated": fixed,
        "remediations": remediations,
    }


# --- Remediation handlers ----------------------------------------------------


def _remediate_api_down(failure: dict[str, Any]) -> dict[str, Any]:
    """Restart the forgescaler ECS service."""
    if MODE != "production":
        return {"fixed": True, "action": "restart_forgescaler", "detail": "mock restart"}
    try:
        from nexus.capabilities.registry import registry

        result = registry.execute("restart_service",
                                  cluster=FORGEWING_CLUSTER, service="forgescaler")
        if result.ok:
            return {"fixed": True, "action": "restart_forgescaler",
                    "detail": "Force-deployed forgescaler service"}
        return {"fixed": False, "action": "restart_failed",
                "detail": result.error or "unknown"}
    except Exception as exc:
        return {"fixed": False, "action": "restart_failed", "detail": str(exc)[:200]}


def _remediate_missing_brief(failure: dict[str, Any]) -> dict[str, Any]:
    """Trigger brief regeneration for the test tenant."""
    from nexus.synthetic_tests import TEST_TENANT

    resp = call_api("POST", f"/brief/{TEST_TENANT}/regenerate", timeout=15)
    if resp.get("error"):
        return {"fixed": False, "action": "regenerate_failed",
                "detail": resp["error"][:200]}
    return {"fixed": True, "action": "regenerate_brief",
            "detail": f"Brief regeneration triggered for {TEST_TENANT[:12]}"}


def _remediate_deploy(failure: dict[str, Any]) -> dict[str, Any]:
    """Run deploy readiness check via existing capability."""
    from nexus.synthetic_tests import TEST_TENANT

    try:
        from nexus.capabilities.registry import registry

        result = registry.execute("check_deploy_readiness", tenant_id=TEST_TENANT)
        if result.ok:
            return {"fixed": True, "action": "check_deploy_readiness",
                    "detail": "Readiness check executed"}
        return {"fixed": False, "action": "readiness_failed",
                "detail": result.error or "unknown"}
    except Exception as exc:
        return {"fixed": False, "action": "readiness_failed", "detail": str(exc)[:200]}


def _record(journey_name: str, result: dict[str, Any]) -> None:
    """Record remediation attempt in the Overwatch graph."""
    try:
        overwatch_graph.record_healing_action(
            action_type=f"synthetic_remediation:{journey_name}",
            target="forgewing",
            blast_radius=BLAST_MODERATE,
            trigger="synthetic_tests",
            outcome="success" if result.get("fixed") else "failed",
        )
    except Exception:
        pass

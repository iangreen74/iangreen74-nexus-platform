"""
CI/CD gates called by aria-platform's pipeline.

- check_deploy_drift()   — do running ECS services share a commit SHA?
- evaluate_ci_gate()     — should CI proceed with a deploy?
- run_synthetic_suite()  — fire all synthetic journeys now
- verify_deploy()        — post-deploy combined check

Fail-open convention: telemetry outage warns, never blocks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- 1. Deploy drift ---------------------------------------------------------


def check_deploy_drift() -> dict[str, Any]:
    """Drift = different image digests among customer-cluster services
    that should share a release. aria-console excluded (own cycle)."""
    from nexus import aws_client
    from nexus.config import MODE, SERVICE_CLUSTERS

    if MODE != "production":
        return {
            "drift": False,
            "services": {s: {"image": "mock", "digest": "mock", "cluster": c}
                         for s, c in SERVICE_CLUSTERS.items()},
            "unique_digests": [],
            "recommendation": "ALIGNED",
            "mock": True,
        }

    services: dict[str, dict[str, Any]] = {}
    for name, cluster in SERVICE_CLUSTERS.items():
        try:
            ecs = aws_client._client("ecs")
            tasks = ecs.list_tasks(cluster=cluster, serviceName=name).get("taskArns", [])
            if not tasks:
                services[name] = {"status": "no_tasks", "cluster": cluster}
                continue
            desc = ecs.describe_tasks(cluster=cluster, tasks=tasks[:1]).get("tasks", [])
            if not desc:
                services[name] = {"status": "describe_empty", "cluster": cluster}
                continue
            container = (desc[0].get("containers") or [{}])[0]
            image = container.get("image", "")
            digest = container.get("imageDigest", "") or ""
            services[name] = {
                "image": image,
                "digest": digest,
                "cluster": cluster,
                "status": container.get("lastStatus") or desc[0].get("lastStatus") or "?",
            }
        except Exception as exc:
            logger.debug("drift check for %s (cluster=%s) failed", name, cluster, exc_info=True)
            services[name] = {"status": "error", "cluster": cluster, "error": str(exc)[:200]}

    # Drift is only meaningful within the customer cluster — aria-console
    # ships independently so its digest shouldn't count.
    from nexus.config import FORGEWING_CLUSTER
    customer_digests = {
        v.get("digest") for v in services.values()
        if v.get("cluster") == FORGEWING_CLUSTER and v.get("digest")
    }
    drift = len(customer_digests) > 1
    return {
        "drift": drift,
        "services": services,
        "unique_digests": sorted(customer_digests),
        "recommendation": "DRIFT_DETECTED" if drift else "ALIGNED",
        "checked_at": _now_iso(),
    }


# --- 2. CI gate --------------------------------------------------------------


def evaluate_ci_gate(commit_sha: str = "") -> dict[str, Any]:
    """Should a production deploy proceed? Thin wrapper over the existing
    CI Decision Engine. Honors an active operator override before running
    the normal evaluation. Fail-open on any check that raises."""
    from nexus.capabilities.ci_decision_engine import evaluate_deploy_readiness
    from nexus.capabilities.ci_gate_override import get_active_override

    try:
        override = get_active_override()
    except Exception:
        override = None
    if override:
        return {
            "decision": override.get("decision", "DEPLOY"),
            "source": "manual_override", "commit": commit_sha,
            "reason": override.get("reason", ""),
            "override_expires_at": override.get("expires_at"),
            "override_created_at": override.get("created_at"),
            "blockers": [], "warnings": [], "checks_run": 0,
            "timestamp": _now_iso(),
        }

    try:
        readiness = evaluate_deploy_readiness()
    except Exception as exc:
        logger.warning("readiness engine failed: %s", exc)
        return {
            "decision": "DEPLOY",
            "commit": commit_sha,
            "blockers": [],
            "warnings": [f"readiness engine unavailable: {str(exc)[:120]}"],
            "checks_run": 0,
            "timestamp": _now_iso(),
            "fail_open": True,
        }

    engine_decision = readiness.get("decision", "DEPLOY")
    if engine_decision == "HOLD":
        decision = "HOLD"
    else:
        decision = "DEPLOY"

    return {
        "decision": decision,
        "source": "readiness_engine",
        "engine_decision": engine_decision,
        "commit": commit_sha,
        "reason": readiness.get("reason", ""),
        "blockers": readiness.get("blockers", []),
        "warnings": readiness.get("warnings", []),
        "factors": readiness.get("factors", {}),
        "checks_run": len(readiness.get("factors", {})),
        "timestamp": _now_iso(),
    }


# --- 3. Synthetic suite on demand --------------------------------------------


def run_synthetic_suite(trigger: str = "manual", commit: str = "") -> dict[str, Any]:
    """Force-run all synthetic journeys. Returns shaped results."""
    from nexus.synthetic_tests import run_all_journeys

    try:
        results = run_all_journeys(force=True)
    except Exception as exc:
        logger.warning("synthetic suite failed: %s", exc)
        return {
            "verdict": "ERROR",
            "trigger": trigger,
            "commit": commit,
            "error": str(exc)[:200],
            "timestamp": _now_iso(),
        }

    passed = sum(1 for r in results if r.get("status") == "pass")
    skipped = sum(1 for r in results if r.get("status") == "skip")
    failed = [r for r in results if r.get("status") in ("fail", "error")]
    total = len(results)
    effective_total = total - skipped
    verdict = ("EMPTY" if effective_total == 0
               else "PASS" if passed == effective_total else "DEGRADED")

    return {
        "verdict": verdict,
        "passed": passed,
        "skipped": skipped,
        "total": total,
        "effective_total": effective_total,
        "failed_tests": [f.get("name") for f in failed],
        "trigger": trigger,
        "commit": commit,
        "results": results,
        "timestamp": _now_iso(),
    }


# --- 4. Post-deploy combined verification ------------------------------------


def verify_deploy(expected_sha: str = "") -> dict[str, Any]:
    """Drift + synthetic in a single call, for CI post-deploy."""
    drift = check_deploy_drift()
    tests = run_synthetic_suite(trigger="deploy-verify", commit=expected_sha)
    aligned = not drift.get("drift", False)
    verdict = "VERIFIED" if aligned and tests["verdict"] == "PASS" else "ISSUES_DETECTED"
    return {
        "expected_commit": expected_sha,
        "drift": drift,
        "synthetic_tests": tests,
        "verdict": verdict,
        "timestamp": _now_iso(),
    }

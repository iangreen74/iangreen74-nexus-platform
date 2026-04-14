"""
Feature Health registry + evaluator.

FEATURES maps product capabilities to health signals (synthetic test
names + health-check function names). get_all_feature_health() evaluates
every feature in parallel and returns the tile data.

Per-feature status is the worst of:
  - any error   → critical (>1 err) or degraded (1 err)
  - any warning → warning
  - otherwise   → healthy

Overall platform status is the worst feature status.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from nexus.capabilities.feature_checks import HEALTH_CHECKS

logger = logging.getLogger(__name__)

# Synthetic suite results don't change every poll — cache for a minute so
# the 30s dashboard refresh isn't paying for the same lookup twice.
_synthetic_cache: dict[str, Any] = {"results": {}, "timestamp": 0.0}
_SYNTHETIC_TTL_SEC = 60.0
# 10s gives cross-service calls (Neptune, Forgewing API) headroom on a cold
# cache; the parallel-fanout means total Phase 1 still finishes well under
# the dashboard's 30s poll interval.
_CHECK_TIMEOUT_SEC = 10.0

# Top-level result cache for the whole /api/feature-health payload.
# The dashboard polls every 60s and keeps this warm; Goal Phase 1 reads
# from it (via get_cached_feature_health) instead of re-evaluating all
# six features against Neptune/Forgewing, which routinely exceeds 10s.
_health_cache: dict[str, Any] = {"data": None, "timestamp": 0.0}
_HEALTH_TTL_SEC = 90.0


FEATURES: dict[str, dict[str, Any]] = {
    "projects": {
        "name": "Projects",
        "description": "Multi-project system, isolation, Start from Scratch",
        "icon": "📁",
        "synthetic_tests": [
            "project_list", "project_separation", "sfs_detection",
            "brief_project_isolation", "sfs_project_creation",
            "project_delete_cleanup",
            "ingestion_completion", "connect_flow_health", "sfs_flow_health",
        ],
        "health_checks": ["check_project_isolation", "check_sfs_health"],
    },
    "aria_chat": {
        "name": "ARIA Chat",
        "description": "Conversation, research mode, phase-awareness",
        "icon": "💬",
        "synthetic_tests": [
            "conversation_scoping", "conversation_no_bleed", "conversation_scoped",
        ],
        "health_checks": ["check_chat_health", "check_bedrock_latency"],
    },
    "code_generation": {
        "name": "Code Generation",
        "description": "PR pipeline, Accretion Core, task dispatch",
        "icon": "⚙️",
        "synthetic_tests": ["brief_exists", "brief_scoped"],
        "health_checks": ["check_daemon_dispatch", "check_pr_pipeline"],
    },
    "deployment": {
        "name": "Deployment",
        "description": "Tier 1/2/3 deploy, CloudFormation, CodeBuild",
        "icon": "🚀",
        "synthetic_tests": [
            "deploy_readiness", "actions_reflect_reality",
            "ci_monitoring_health", "ci_healer_readiness",
        ],
        "health_checks": ["check_deploy_health", "check_stuck_deploys"],
    },
    "onboarding": {
        "name": "Onboarding",
        "description": "Signup → Stripe → GitHub → first project",
        "icon": "🎯",
        "synthetic_tests": [
            "health", "github_banner_consistency", "action_banner_freshness",
        ],
        "health_checks": ["check_onboarding_pipeline", "check_tenant_stages"],
    },
    "intelligence": {
        "name": "Intelligence",
        "description": "Accretion Core, Omniscience, Brief synthesis",
        "icon": "🧠",
        "synthetic_tests": ["brief_exists", "status_scoped"],
        "health_checks": ["check_intelligence_sources", "check_brief_freshness"],
    },
}


def _synthetic_results_by_name() -> dict[str, str]:
    """Map journey name → status from the last synthetic suite run."""
    try:
        from nexus.synthetic_tests import get_summary

        summary = get_summary() or {}
        out: dict[str, str] = {}
        for r in summary.get("results", []) or []:
            if isinstance(r, dict) and r.get("name"):
                out[r["name"]] = r.get("status", "unknown")
        return out
    except Exception:
        logger.debug("synthetic summary unavailable", exc_info=True)
        return {}


async def _run_check_with_timeout(check_name: str, fn: Any,
                                    timeout: float = _CHECK_TIMEOUT_SEC) -> dict[str, Any]:
    """Invoke a sync health check in a thread with a hard timeout."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout) or {}
    except asyncio.TimeoutError:
        return {"status": "warning", "message": f"{check_name} timed out ({timeout}s)"}
    except Exception as exc:
        return {"status": "warning", "message": f"{type(exc).__name__}: {str(exc)[:100]}"}


def _evaluate_feature(fid: str, fdef: dict[str, Any],
                       synthetics: dict[str, str]) -> dict[str, Any]:
    """Synchronous evaluator — kept for callers that already have results.

    For the dashboard path use _evaluate_feature_async which adds per-check
    timeouts so a slow Neptune query can't stall the whole tile.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for test_name in fdef.get("synthetic_tests", []) or []:
        status = synthetics.get(test_name, "not_run")
        if status in ("fail", "error"):
            errors.append(f"Synthetic '{test_name}' {status}")
        elif status == "not_run":
            warnings.append(f"Synthetic '{test_name}' not running")
        # pass + skip both count as healthy for this signal

    for check_name in fdef.get("health_checks", []) or []:
        fn = HEALTH_CHECKS.get(check_name)
        if not fn:
            warnings.append(f"Unknown health check {check_name}")
            continue
        try:
            result = fn() or {}
            status = result.get("status", "ok")
            if status == "error":
                errors.append(result.get("message", check_name))
            elif status == "warning":
                warnings.append(result.get("message", check_name))
        except Exception as exc:
            warnings.append(f"{check_name}: {type(exc).__name__}: {str(exc)[:100]}")

    return _build_status(fid, fdef, errors, warnings)


async def _evaluate_feature_async(fid: str, fdef: dict[str, Any],
                                    synthetics: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    for test_name in fdef.get("synthetic_tests", []) or []:
        status = synthetics.get(test_name, "not_run")
        if status in ("fail", "error"):
            errors.append(f"Synthetic '{test_name}' {status}")
        elif status == "not_run":
            warnings.append(f"Synthetic '{test_name}' not running")

    check_names = fdef.get("health_checks", []) or []
    tasks = []
    for check_name in check_names:
        fn = HEALTH_CHECKS.get(check_name)
        if not fn:
            warnings.append(f"Unknown health check {check_name}")
            continue
        tasks.append((check_name, _run_check_with_timeout(check_name, fn)))

    if tasks:
        results = await asyncio.gather(*[t for _, t in tasks], return_exceptions=False)
        for (check_name, _), result in zip(tasks, results):
            status = (result or {}).get("status", "ok")
            if status == "error":
                errors.append(result.get("message", check_name))
            elif status == "warning":
                warnings.append(result.get("message", check_name))

    return _build_status(fid, fdef, errors, warnings)


def _build_status(fid: str, fdef: dict[str, Any],
                    errors: list[str], warnings: list[str]) -> dict[str, Any]:
    if len(errors) > 1:
        status = "critical"
    elif errors:
        status = "degraded"
    elif warnings:
        status = "warning"
    else:
        status = "healthy"

    status_line = (errors[0] if errors else
                   warnings[0] if warnings else
                   "All checks passing")

    return {
        "id": fid,
        "name": fdef["name"],
        "description": fdef["description"],
        "icon": fdef["icon"],
        "status": status,
        "status_line": status_line,
        "errors": len(errors),
        "warnings": len(warnings),
        "error_details": errors,
        "warning_details": warnings,
    }


async def _get_synthetic_results_cached() -> dict[str, str]:
    """Synthetic suite results, cached for _SYNTHETIC_TTL_SEC."""
    now = time.time()
    if now - _synthetic_cache["timestamp"] < _SYNTHETIC_TTL_SEC and _synthetic_cache["results"]:
        return _synthetic_cache["results"]
    results = await asyncio.to_thread(_synthetic_results_by_name)
    _synthetic_cache["results"] = results
    _synthetic_cache["timestamp"] = now
    return results


async def get_all_feature_health() -> dict[str, Any]:
    """Evaluate every feature. Returns tile data for the dashboard."""
    synthetics = await _get_synthetic_results_cached()

    # Run all feature evaluations in parallel — each one fans out to its own
    # health checks under a per-check timeout, so a slow Neptune query in one
    # tile can't stall the whole dashboard.
    fids = list(FEATURES.keys())
    coros = [_evaluate_feature_async(fid, FEATURES[fid], synthetics) for fid in fids]
    results = await asyncio.gather(*coros, return_exceptions=True)
    features: dict[str, Any] = {}
    for fid, result in zip(fids, results):
        if isinstance(result, Exception):
            fdef = FEATURES[fid]
            features[fid] = {
                "id": fid, "name": fdef["name"], "description": fdef["description"],
                "icon": fdef["icon"], "status": "warning",
                "status_line": f"{type(result).__name__}: {str(result)[:120]}",
                "errors": 0, "warnings": 1,
                "error_details": [], "warning_details": [str(result)[:200]],
            }
        else:
            features[fid] = result

    statuses = {f["status"] for f in features.values()}
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"

    payload = {
        "overall": overall,
        "features": features,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _health_cache["data"] = payload
    _health_cache["timestamp"] = time.time()
    return payload


def get_cached_feature_health() -> dict[str, Any] | None:
    """Return the last full-feature-health payload if still fresh, else None.

    Read-only — never triggers a fresh evaluation. Goal Phase 1 uses this
    so the rollup is instant when the dashboard cache is warm.
    """
    if not _health_cache.get("data"):
        return None
    if time.time() - _health_cache.get("timestamp", 0) > _HEALTH_TTL_SEC:
        return None
    return _health_cache["data"]

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
from datetime import datetime, timezone
from typing import Any

from nexus.capabilities.feature_checks import HEALTH_CHECKS

logger = logging.getLogger(__name__)


FEATURES: dict[str, dict[str, Any]] = {
    "projects": {
        "name": "Projects",
        "description": "Multi-project system, isolation, Start from Scratch",
        "icon": "📁",
        "synthetic_tests": ["project_list", "project_separation", "sfs_detection"],
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
        "synthetic_tests": ["deploy_readiness", "actions_reflect_reality"],
        "health_checks": ["check_deploy_health", "check_stuck_deploys"],
    },
    "onboarding": {
        "name": "Onboarding",
        "description": "Signup → Stripe → GitHub → first project",
        "icon": "🎯",
        "synthetic_tests": ["health"],
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


def _evaluate_feature(fid: str, fdef: dict[str, Any],
                       synthetics: dict[str, str]) -> dict[str, Any]:
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


async def get_all_feature_health() -> dict[str, Any]:
    """Evaluate every feature. Returns tile data for the dashboard."""
    # Synthetic results once, reused across features.
    synthetics = await asyncio.to_thread(_synthetic_results_by_name)

    # Per-feature evaluation is cheap (in-process checks), run sequentially
    # to keep the call graph predictable.
    features = {
        fid: _evaluate_feature(fid, fdef, synthetics)
        for fid, fdef in FEATURES.items()
    }

    statuses = {f["status"] for f in features.values()}
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "features": features,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

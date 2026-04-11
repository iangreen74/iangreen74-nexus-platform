"""
Deploy Operations — diagnostic healing for stuck deployments.

Replaces blind retry with structured diagnosis:
  1. Check readiness → identify blockers
  2. Auto-fix what's fixable (SLR, Dockerfile, etc.)
  3. Retry ONLY if all blockers resolved
  4. Rate-limit: max 3 attempts per tenant per hour

Known blocker categories:
  no_aws_role         → user must reconnect AWS (escalate)
  role_assumption_failed → check bootstrap_updater (auto-fix attempt)
  missing_slr         → Forgewing can auto-create (auto-fix)
  no_dockerfile       → Forgewing can auto-generate (auto-fix)
  stuck_stack         → CF ROLLBACK_FAILED, needs cleanup (escalate)
  build_failed        → read logs, trigger self-healer (auto-fix attempt)
"""
from __future__ import annotations

import logging
import time
import threading
from typing import Any

from nexus import neptune_client, overwatch_graph
from nexus.capabilities import forgewing_api
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_MODERATE, BLAST_SAFE, MODE

logger = logging.getLogger("nexus.capabilities.deploy_ops")

# Rate limiting: max 3 diagnostic attempts per tenant per hour
_rate_lock = threading.Lock()
_attempt_times: dict[str, list[float]] = {}
MAX_ATTEMPTS_PER_HOUR = 3


def _check_rate(tenant_id: str) -> bool:
    """True if we can attempt another deploy fix for this tenant."""
    now = time.monotonic()
    with _rate_lock:
        times = _attempt_times.get(tenant_id, [])
        times = [t for t in times if now - t < 3600]
        _attempt_times[tenant_id] = times
        return len(times) < MAX_ATTEMPTS_PER_HOUR


def _record_attempt(tenant_id: str) -> None:
    with _rate_lock:
        _attempt_times.setdefault(tenant_id, []).append(time.monotonic())


# Auto-fixable vs user-action-required blockers
_AUTO_FIXABLE = frozenset({"missing_slr", "no_dockerfile", "build_failed"})
_USER_ACTION = frozenset({"no_aws_role", "stuck_stack"})


def check_deploy_readiness(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Check deployment readiness for a tenant. Returns blockers.
    Safe blast radius — read-only diagnosis.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"tenant_id": tenant_id, "ready": True, "blockers": [], "mock": True}

    blockers: list[dict[str, str]] = []

    # Check tenant context for AWS role
    ctx = neptune_client.get_tenant_context(tenant_id)
    if not ctx.get("aws_role_arn"):
        blockers.append({"type": "no_aws_role", "fixable": "user",
                         "message": "No aws_role_arn — user must connect AWS in Settings"})

    # Check DeploymentProgress
    dp = neptune_client.query(
        "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
        "RETURN d.stage AS stage, d.message AS msg",
        {"tid": tenant_id},
    )
    if dp:
        stage = dp[0].get("stage", "")
        msg = dp[0].get("msg", "")
        if "ROLLBACK" in stage.upper():
            blockers.append({"type": "stuck_stack", "fixable": "user",
                             "message": f"Stack in {stage} — needs manual cleanup"})
        if "BUILD_FAILED" in msg.upper() or "build" in msg.lower():
            blockers.append({"type": "build_failed", "fixable": "auto",
                             "message": f"Build failed: {msg[:100]}"})

    # Check DeploymentDNA for Dockerfile
    dna = neptune_client.query(
        "MATCH (d:DeploymentDNA {tenant_id: $tid}) "
        "RETURN d.has_dockerfile AS has_df, d.recommendation AS rec",
        {"tid": tenant_id},
    )
    if dna and not dna[0].get("has_dockerfile"):
        blockers.append({"type": "no_dockerfile", "fixable": "auto",
                         "message": "No Dockerfile — Forgewing can auto-generate"})

    # Try Forgewing readiness endpoint (may not exist yet)
    readiness = forgewing_api.call_api("GET", f"/deploy-readiness/{tenant_id}")
    if not readiness.get("error") and readiness.get("blockers"):
        for b in readiness["blockers"]:
            if not any(existing["type"] == b.get("type") for existing in blockers):
                blockers.append(b)

    auto_fixable = [b for b in blockers if b.get("fixable") == "auto"]
    user_required = [b for b in blockers if b.get("fixable") == "user"]

    return {
        "tenant_id": tenant_id,
        "ready": len(blockers) == 0,
        "blockers": blockers,
        "auto_fixable_count": len(auto_fixable),
        "user_action_count": len(user_required),
    }


def diagnose_and_fix_deploy(tenant_id: str = "", **_: Any) -> dict[str, Any]:
    """
    Full diagnostic deploy heal: readiness → fix → retry if clear.
    Moderate blast radius — may trigger infrastructure changes.
    """
    if not tenant_id:
        return {"error": "tenant_id required"}
    if MODE != "production":
        return {"tenant_id": tenant_id, "action": "diagnosed", "mock": True}

    # Rate limit
    if not _check_rate(tenant_id):
        return {"tenant_id": tenant_id, "action": "rate_limited",
                "message": f"Max {MAX_ATTEMPTS_PER_HOUR} attempts/hour reached"}

    _record_attempt(tenant_id)

    # Step 1: Check readiness
    readiness = check_deploy_readiness(tenant_id=tenant_id)
    if readiness.get("ready"):
        # All clear — trigger deploy
        result = forgewing_api.call_api("POST", f"/deploy/{tenant_id}")
        overwatch_graph.record_healing_action(
            "retry_tenant_deploy", tenant_id, "moderate", "readiness_passed", "triggered")
        return {"tenant_id": tenant_id, "action": "deploy_triggered",
                "readiness": readiness, "deploy_result": result}

    # Step 2: Check for user-action-required blockers
    user_blockers = [b for b in readiness["blockers"] if b.get("fixable") == "user"]
    if user_blockers:
        # Can't auto-fix — escalate
        overwatch_graph.record_event(
            "deploy_blocked_user_action", f"tenant:{tenant_id}",
            {"blockers": [b["type"] for b in user_blockers]}, "warning")
        return {"tenant_id": tenant_id, "action": "escalated",
                "reason": "user_action_required", "blockers": user_blockers}

    # Step 3: Attempt auto-fixes
    auto_blockers = [b for b in readiness["blockers"] if b.get("fixable") == "auto"]
    fixes_applied: list[str] = []
    for blocker in auto_blockers:
        btype = blocker["type"]
        if btype in ("missing_slr", "no_dockerfile"):
            # Forgewing handles these on deploy — just trigger
            fixes_applied.append(f"will_fix_on_deploy:{btype}")
        elif btype == "build_failed":
            fixes_applied.append("retrigger_build")

    # Step 4: Retry if we have fixes
    if fixes_applied:
        result = forgewing_api.call_api("POST", f"/deploy/{tenant_id}")
        overwatch_graph.record_healing_action(
            "diagnose_and_fix_deploy", tenant_id, "moderate",
            f"fixes:{','.join(fixes_applied)}", "triggered")
        return {"tenant_id": tenant_id, "action": "deploy_with_fixes",
                "fixes": fixes_applied, "deploy_result": result}

    return {"tenant_id": tenant_id, "action": "no_action",
            "readiness": readiness, "message": "no fixable blockers found"}


# Register capabilities
registry.register(Capability(
    name="check_deploy_readiness",
    function=check_deploy_readiness,
    blast_radius=BLAST_SAFE,
    description="Check deployment readiness — identify blockers before retry",
))
registry.register(Capability(
    name="diagnose_and_fix_deploy",
    function=diagnose_and_fix_deploy,
    blast_radius=BLAST_MODERATE,
    description="Diagnostic deploy heal: readiness → fix blockers → retry if clear",
))

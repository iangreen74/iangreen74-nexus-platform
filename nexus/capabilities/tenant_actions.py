"""Write ActionRequired nodes for user-facing blockers.

Overwatch detects conditions that need user intervention and upserts
ActionRequired nodes into the shared Neptune graph. Forgewing reads
these to render the dashboard ActionBanner.

Templates define the user-facing copy; check_and_create_actions looks
at a tenant_health report and decides which actions to create/clear.
Each action MERGEs on (tenant_id, action_type), so repeated calls
refresh rather than duplicate.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus import overwatch_graph

logger = logging.getLogger("nexus.tenant_actions")


ACTION_TEMPLATES: dict[str, dict[str, str]] = {
    "no_cloud_connected": {
        "severity": "high",
        "title": "Connect a cloud account to deploy",
        "message": (
            "Your code is being built but can't be deployed yet. "
            "Connect AWS, Google Cloud, or Azure in Settings."
        ),
        "button_label": "Connect Cloud",
        "destination": "/settings/{tid}#cloud",
        "category": "onboarding",
    },
    "deploy_stuck": {
        "severity": "high",
        "title": "Deployment needs attention",
        "message": (
            "Your deployment has been stuck. Check Settings to verify "
            "your cloud connection, or ask ARIA for help."
        ),
        "button_label": "Check Settings",
        "destination": "/settings/{tid}",
        "category": "deploy",
    },
    "github_token_expiring": {
        "severity": "medium",
        "title": "GitHub connection needs renewal",
        "message": (
            "Your GitHub access will expire soon. "
            "Re-authorize in Settings to keep ARIA working."
        ),
        "button_label": "Reconnect GitHub",
        "destination": "/settings/{tid}#github",
        "category": "connection",
    },
    "ci_failing_repeatedly": {
        "severity": "medium",
        "title": "Code checks are failing",
        "message": (
            "Recent changes aren't passing automated checks. "
            "Ask ARIA to diagnose the issue."
        ),
        "button_label": "Ask ARIA",
        "destination": "chat",
        "category": "ci",
    },
    "pr_awaiting_review": {
        "severity": "low",
        "title": "Changes ready for your review",
        "message": "{count} pull request(s) are waiting for your approval.",
        "button_label": "Review Changes",
        "destination": "/mission/{tid}?view=dashboard&phase=build",
        "category": "review",
    },
}


def create_action(
    tenant_id: str,
    action_type: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """Upsert an ActionRequired node for this tenant."""
    template = ACTION_TEMPLATES.get(action_type)
    if not template:
        logger.warning("Unknown action type: %s", action_type)
        return
    format_vars = {"tid": tenant_id, **(extra or {})}
    try:
        message = template["message"].format(**format_vars)
        button = template["button_label"].format(**format_vars) if "{" in template["button_label"] else template["button_label"]
        destination = template["destination"].format(**format_vars) if "{" in template["destination"] else template["destination"]
    except KeyError as exc:
        logger.warning("action %s missing format var %s", action_type, exc)
        return
    overwatch_graph.write_tenant_action(
        tenant_id,
        action_type,
        {
            "severity": template["severity"],
            "title": template["title"],
            "message": message,
            "button_label": button,
            "destination": destination,
            "category": template["category"],
        },
    )
    logger.info("Action created: %s for %s", action_type, tenant_id[:12])


def clear_action(tenant_id: str, action_type: str) -> None:
    """Remove an ActionRequired when the condition is resolved."""
    overwatch_graph.clear_tenant_action(tenant_id, action_type)


# Stages at which the user is expected to have a cloud connected.
_POST_BRIEF_STAGES = {
    "executing",
    "complete",
    "brief_pending_approval",
    "brief_approved",
    "deploying",
}


def check_and_create_actions(tenant_id: str, tenant_data: dict[str, Any]) -> None:
    """
    Inspect a tenant_health report and upsert/clear ActionRequired nodes.

    Called at the end of per-tenant triage. Never raises — the caller
    wraps in try/except but we still guard each action independently.
    """
    if not tenant_id or not isinstance(tenant_data, dict):
        return

    ctx = tenant_data.get("context") or {}
    deployment = tenant_data.get("deployment") or {}
    pipeline = tenant_data.get("pipeline") or {}
    stage = (ctx.get("mission_stage") or "").strip()

    # --- no_cloud_connected ---
    try:
        past_brief = stage in _POST_BRIEF_STAGES
        # deployment.provisioned == False means the deploy stack was never
        # created, which for post-brief tenants means no cloud creds.
        provisioned = deployment.get("provisioned")
        if past_brief and provisioned is False:
            create_action(tenant_id, "no_cloud_connected")
        elif provisioned is True:
            clear_action(tenant_id, "no_cloud_connected")
    except Exception:
        logger.debug("no_cloud_connected check failed for %s", tenant_id, exc_info=True)

    # --- deploy_stuck ---
    try:
        if tenant_data.get("deploy_stuck"):
            create_action(tenant_id, "deploy_stuck")
        else:
            clear_action(tenant_id, "deploy_stuck")
    except Exception:
        logger.debug("deploy_stuck check failed for %s", tenant_id, exc_info=True)

    # --- pr_awaiting_review ---
    # Only fire if pipeline surfaces an explicit "in_review" count.
    # Our tenant_health schema doesn't track review-state yet; when it
    # does, the action will start firing without further changes here.
    try:
        in_review = pipeline.get("prs_in_review") or pipeline.get("in_review") or 0
        if isinstance(in_review, int) and in_review > 0:
            create_action(tenant_id, "pr_awaiting_review", extra={"count": in_review})
        else:
            clear_action(tenant_id, "pr_awaiting_review")
    except Exception:
        logger.debug("pr_awaiting_review check failed for %s", tenant_id, exc_info=True)

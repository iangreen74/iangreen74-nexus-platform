"""
Deploy Pattern Learning — builds operational intelligence from outcomes.

Every deploy outcome (success/failure/rollback) is recorded. Over time,
Overwatch learns patterns like:
- "commits touching daemon_actions.py have 15% rollback rate"
- "deploys during active heal chains fail 30% more often"
- "Friday afternoon deploys fail 2x more than Tuesday morning"

Outcomes are stored as OverwatchDeployOutcome nodes in the Overwatch
graph (local-mode: in-memory list). The deploy_decision engine reads
these to compute recent failure rates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger(__name__)


def record_deploy_outcome(outcome: dict[str, Any]) -> str:
    """Record a deploy outcome for pattern learning.

    Args:
        outcome: {commit_sha, service, environment, status,
                  timestamp, changed_files, commit_message, run_url,
                  risk_score, decision_was}

    Returns:
        The node id of the recorded outcome.
    """
    sha = outcome.get("commit_sha", "")[:8]
    svc = outcome.get("service", "?")
    status = outcome.get("status", "unknown")
    logger.info("Deploy outcome: %s/%s → %s", svc, sha, status)

    node_id = overwatch_graph.record_event(
        event_type="deploy_outcome",
        service=outcome.get("service", ""),
        severity="info" if status == "success" else "warning",
        details={
            "commit_sha": outcome.get("commit_sha", ""),
            "status": status,
            "environment": outcome.get("environment", ""),
            "changed_files": outcome.get("changed_files", 0),
            "commit_message": (outcome.get("commit_message") or "")[:200],
            "run_url": outcome.get("run_url", ""),
            "risk_score": outcome.get("risk_score"),
            "decision_was": outcome.get("decision_was", ""),
            "timestamp": outcome.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
        },
    )
    return node_id


def get_deploy_failure_count(hours: int = 6) -> int:
    """Count deploy failures in the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    events = overwatch_graph.get_recent_events(limit=200)
    count = 0
    for e in events:
        if e.get("event_type") != "deploy_outcome":
            continue
        if e.get("created_at", "") < cutoff:
            continue
        details = e.get("details") or {}
        if isinstance(details, str):
            import json

            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        if details.get("status") in ("failed", "rollback"):
            count += 1
    return count


def get_deploy_success_rate(hours: int = 24) -> dict[str, Any]:
    """Compute deploy success rate over the last N hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    events = overwatch_graph.get_recent_events(limit=500)
    total = 0
    failed = 0
    for e in events:
        if e.get("event_type") != "deploy_outcome":
            continue
        if e.get("created_at", "") < cutoff:
            continue
        total += 1
        details = e.get("details") or {}
        if isinstance(details, str):
            import json

            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        if details.get("status") in ("failed", "rollback"):
            failed += 1
    rate = (total - failed) / total if total > 0 else 1.0
    return {"rate": round(rate, 3), "total": total, "failed": failed}

"""Neptune queries used by the Learning Intelligence Report."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph

logger = logging.getLogger(__name__)


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def recent_dogfood_runs(hours: int = 168) -> list[dict[str, Any]]:
    return overwatch_graph.query(
        "MATCH (r:OverwatchDogfoodRun) WHERE r.created_at >= $since "
        "RETURN r.id AS run_id, r.status AS status, "
        "r.outcome AS outcome, r.app_name AS app, "
        "r.repo_name AS repo, r.created_at AS created, "
        "r.batch_id AS batch_id, r.tenant_id AS tenant_id, "
        "r.project_id AS project_id, r.failure_message AS reason "
        "ORDER BY r.created_at DESC LIMIT 500",
        {"since": _since(hours)},
    ) or []


def deploy_attempts(hours: int = 168) -> list[dict[str, Any]]:
    try:
        return overwatch_graph.query(
            "MATCH (d:DeployAttempt) WHERE d.created_at >= $since "
            "RETURN d.fingerprint AS fingerprint, d.outcome AS outcome, "
            "d.language AS language, d.framework AS framework, "
            "d.template_quality_score AS quality, "
            "d.tenant_id AS tenant_id, d.project_id AS project_id, "
            "d.created_at AS created "
            "ORDER BY d.created_at DESC LIMIT 500",
            {"since": _since(hours)},
        ) or []
    except Exception:
        return []


def mission_tasks_for_runs(
    project_ids: list[str],
) -> list[dict[str, Any]]:
    if not project_ids:
        return []
    try:
        return overwatch_graph.query(
            "MATCH (t:MissionTask) WHERE t.project_id IN $pids "
            "RETURN t.project_id AS project_id, t.task_index AS idx, "
            "t.status AS status, t.pr_url AS pr_url "
            "ORDER BY t.project_id, t.task_index",
            {"pids": project_ids},
        ) or []
    except Exception:
        return []


def pattern_fingerprint_counts() -> tuple[int, int]:
    try:
        total = overwatch_graph.query(
            "MATCH (p:PatternFingerprint) RETURN count(p) AS c"
        )
        unique = overwatch_graph.query(
            "MATCH (p:PatternFingerprint) "
            "RETURN count(DISTINCT p.fingerprint) AS c"
        )
        return (
            int((total[0].get("c") if total else 0) or 0),
            int((unique[0].get("c") if unique else 0) or 0),
        )
    except Exception:
        return (0, 0)


def active_heal_chains() -> list[dict[str, Any]]:
    try:
        return overwatch_graph.query(
            "MATCH (h:OverwatchHealChain) WHERE h.active = true "
            "RETURN h.chain_id AS id, h.kind AS kind LIMIT 20"
        ) or []
    except Exception:
        return []


def bedrock_24h_cost() -> dict[str, Any]:
    try:
        from nexus.capabilities.bedrock_monitor import journey_bedrock_health
        r = journey_bedrock_health()
        return {"cost_usd": 0.0, "call_count": 0, "raw": r}
    except Exception:
        return {"cost_usd": 0.0, "call_count": 0}

"""
Feature-level health checks.

One function per check_* identifier referenced in feature_health.FEATURES.
All return {"status": "ok" | "warning" | "error", "message": str?}.

Each function must never raise — exceptions surface as a warning so a
single broken check can't darken the whole tile.

Queries hit Neptune in production via neptune_client.query (openCypher).
In local mode, they return ok so the dashboard still paints.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus.config import MODE

logger = logging.getLogger(__name__)


def _ok() -> dict[str, Any]:
    return {"status": "ok"}


def _warn(msg: str) -> dict[str, Any]:
    return {"status": "warning", "message": msg}


def _err(msg: str) -> dict[str, Any]:
    return {"status": "error", "message": msg}


# --- Projects ----------------------------------------------------------------


def check_project_isolation() -> dict[str, Any]:
    """MissionTask nodes missing project_id indicate isolation bypass."""
    if MODE != "production":
        return _ok()
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (m:MissionTask) "
            "WHERE m.project_id IS NULL OR m.project_id = '' "
            "RETURN count(m) AS cnt"
        )
        cnt = (rows[0].get("cnt", 0) if rows else 0) or 0
        if cnt > 0:
            return _warn(f"{cnt} MissionTask node(s) missing project_id")
        return _ok()
    except Exception as exc:
        return _warn(str(exc)[:120])


def check_sfs_health() -> dict[str, Any]:
    """Placeholder — wire to SFS recent-activity signal when schema stabilises."""
    return _ok()


# --- ARIA Chat ---------------------------------------------------------------


def check_chat_health() -> dict[str, Any]:
    """Forgewing API health endpoint."""
    try:
        from nexus.capabilities.forgewing_api import call_api

        r = call_api("GET", "/health")
        if not r or r.get("error"):
            return _err(f"Forgewing API: {(r or {}).get('error', 'no response')[:120]}")
        return _ok()
    except Exception as exc:
        return _err(f"Forgewing API unreachable: {str(exc)[:120]}")


def check_bedrock_latency() -> dict[str, Any]:
    """Placeholder — wire to CloudWatch Bedrock metrics when available."""
    return _ok()


# --- Code generation ---------------------------------------------------------


def check_daemon_dispatch() -> dict[str, Any]:
    """Any executing tenant with excessive pending tasks indicates a stuck dispatch."""
    if MODE != "production":
        return _ok()
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (t:Tenant {mission_stage: 'executing'}) "
            "OPTIONAL MATCH (m:MissionTask {tenant_id: t.tenant_id, status: 'pending'}) "
            "WITH t.tenant_id AS tid, count(m) AS pending "
            "WHERE pending > 5 "
            "RETURN tid, pending ORDER BY pending DESC LIMIT 1"
        )
        if rows:
            r = rows[0]
            return _warn(f"Tenant {str(r.get('tid',''))[:12]} has {r.get('pending',0)} pending tasks")
        return _ok()
    except Exception as exc:
        return _warn(str(exc)[:120])


def check_pr_pipeline() -> dict[str, Any]:
    """Placeholder — wire to a PR-throughput signal later."""
    return _ok()


# --- Deployment --------------------------------------------------------------


def check_deploy_health() -> dict[str, Any]:
    """Active heal chains with tenant_deploy_stuck indicate deploy pain."""
    try:
        from nexus.reasoning.executor import get_all_active_chains

        chains = get_all_active_chains() or {}
        stuck = [c for c in chains.values()
                 if isinstance(c, dict) and c.get("chain") == "tenant_deploy_stuck"]
        if stuck:
            return _warn(f"{len(stuck)} tenant_deploy_stuck heal chain(s) active")
        return _ok()
    except Exception as exc:
        return _warn(str(exc)[:120])


def check_stuck_deploys() -> dict[str, Any]:
    """DeploymentProgress nodes at stage=failed."""
    if MODE != "production":
        return _ok()
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (d:DeploymentProgress) WHERE d.stage IN ['failed', 'error'] "
            "RETURN count(d) AS cnt"
        )
        cnt = (rows[0].get("cnt", 0) if rows else 0) or 0
        if cnt > 0:
            return _warn(f"{cnt} deployment(s) at stage=failed")
        return _ok()
    except Exception as exc:
        return _warn(str(exc)[:120])


# --- Onboarding -------------------------------------------------------------


def check_onboarding_pipeline() -> dict[str, Any]:
    """Placeholder — wire to signup→stripe→github transition tracking."""
    return _ok()


def check_tenant_stages() -> dict[str, Any]:
    """Tenant nodes with no mission_stage suggest onboarding incomplete."""
    if MODE != "production":
        return _ok()
    try:
        from nexus import neptune_client

        rows = neptune_client.query(
            "MATCH (t:Tenant) "
            "WHERE t.mission_stage IS NULL OR t.mission_stage = '' "
            "RETURN count(t) AS cnt"
        )
        cnt = (rows[0].get("cnt", 0) if rows else 0) or 0
        if cnt > 0:
            return _warn(f"{cnt} tenant(s) with no mission_stage")
        return _ok()
    except Exception as exc:
        return _warn(str(exc)[:120])


# --- Intelligence -----------------------------------------------------------


def check_intelligence_sources() -> dict[str, Any]:
    """Placeholder — wire to accretion source availability later."""
    return _ok()


def check_brief_freshness() -> dict[str, Any]:
    """Placeholder — wire to brief age-vs-activity signal later."""
    return _ok()


HEALTH_CHECKS: dict[str, Any] = {
    "check_project_isolation": check_project_isolation,
    "check_sfs_health": check_sfs_health,
    "check_chat_health": check_chat_health,
    "check_bedrock_latency": check_bedrock_latency,
    "check_daemon_dispatch": check_daemon_dispatch,
    "check_pr_pipeline": check_pr_pipeline,
    "check_deploy_health": check_deploy_health,
    "check_stuck_deploys": check_stuck_deploys,
    "check_onboarding_pipeline": check_onboarding_pipeline,
    "check_tenant_stages": check_tenant_stages,
    "check_intelligence_sources": check_intelligence_sources,
    "check_brief_freshness": check_brief_freshness,
}

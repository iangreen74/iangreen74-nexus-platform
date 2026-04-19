"""
Dogfood Reconciler — cleans up completed runs and catches orphans.

Runs every daemon cycle:
  1. For each DogfoodRun in terminal state (success/failed/timeout)
     that hasn't been cleaned up AND completed >10 min ago:
       - DELETE the GitHub repo
       - DELETE the Forgewing project (via /admin/projects — correct path)
       - Mark DogfoodRun.cleaned_up = now
  2. For any `df-*` repo on GITHUB_USER older than 2× DOGFOOD_MAX_WAIT_MINUTES
     with no corresponding DogfoodRun node (daemon crash orphan): delete.

Returns a small report dict.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from nexus import neptune_client, overwatch_graph
from nexus.capabilities import forgewing_api
from nexus.config import GITHUB_SECRET_ID, MODE

logger = logging.getLogger("nexus.sensors.dogfood_reconciler")

GITHUB_API = "https://api.github.com"
GITHUB_USER = "iangreen74"
CLEANUP_GRACE_MINUTES = 10  # wait N min after terminal before deleting
ORPHAN_FACTOR = 2  # orphan repo threshold = orphan_factor × max_wait


def _gh_token() -> str:
    if MODE != "production":
        return ""
    try:
        from nexus.aws_client import get_secret
        secret = get_secret(GITHUB_SECRET_ID)
        return secret.get("_raw") or secret.get("github_pat") or secret.get("token") or ""
    except Exception:
        return ""


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _max_wait_minutes() -> int:
    try:
        return int(os.environ.get("DOGFOOD_MAX_WAIT_MINUTES", "90"))
    except (TypeError, ValueError):
        return 90


def _delete_repo(repo: str, token: str) -> bool:
    if MODE != "production":
        return True
    try:
        resp = httpx.delete(
            f"{GITHUB_API}/repos/{GITHUB_USER}/{repo}",
            headers=_gh_headers(token),
            timeout=15,
        )
        return resp.status_code in (202, 204, 404)
    except Exception:
        logger.exception("dogfood reconciler: repo delete failed for %s", repo)
        return False


def _mark_project_cleaned(tenant_id: str, project_id: str, ts: str) -> None:
    """Mark Neptune records as cleaned instead of deleting them."""
    try:
        neptune_client.query(
            "MATCH (n {project_id: $pid, tenant_id: $tid}) "
            "WHERE n:Project OR n:MissionTask OR n:DeploymentProgress "
            "OR n:DeployAttempt SET n.cleaned_up_at = $ts",
            {"pid": project_id, "tid": tenant_id, "ts": ts})
    except Exception:
        logger.debug("mark_project_cleaned failed for %s", project_id)


def _clean_terminal_runs(token: str, now: datetime) -> int:
    cutoff = now - timedelta(minutes=CLEANUP_GRACE_MINUTES)
    cleaned = 0
    for status in ("failed", "timeout"):
        for run in overwatch_graph.list_dogfood_runs(status=status, limit=100):
            if run.get("cleaned_up"):
                continue
            completed = _parse_iso(run.get("completed_at"))
            if completed and completed > cutoff:
                continue  # too fresh — let the operator inspect first
            repo = run.get("repo_name", "")
            project_id = run.get("project_id", "")
            tenant_id = run.get("tenant_id", "")
            if repo:
                _delete_repo(repo, token)
            if project_id and tenant_id:
                _mark_project_cleaned(tenant_id, project_id, now.isoformat())
            overwatch_graph.update_dogfood_run(run.get("id", ""), cleaned_up=now.isoformat())
            cleaned += 1
    return cleaned


def _find_orphan_repos(token: str, now: datetime) -> list[str]:
    """
    List df-* repos older than ORPHAN_FACTOR × max_wait that have no
    DogfoodRun node in the graph. Returns just the repo names.
    """
    if MODE != "production" or not token:
        return []
    try:
        resp = httpx.get(
            f"{GITHUB_API}/users/{GITHUB_USER}/repos?per_page=100&sort=created&direction=desc",
            headers=_gh_headers(token),
            timeout=20,
        )
    except Exception:
        return []
    if resp.status_code != 200:
        return []

    threshold_minutes = _max_wait_minutes() * ORPHAN_FACTOR
    cutoff = now - timedelta(minutes=threshold_minutes)
    known = {r.get("repo_name") for r in overwatch_graph.list_dogfood_runs(limit=200) if r.get("repo_name")}

    orphans: list[str] = []
    for entry in resp.json() or []:
        name = entry.get("name", "")
        if not name.startswith("df-"):
            continue
        if name in known:
            continue
        created = _parse_iso(entry.get("created_at"))
        if created and created < cutoff:
            orphans.append(name)
    return orphans


def reconcile_dogfood() -> dict[str, Any]:
    """Single pass: clean terminal runs + delete untracked df-* orphans."""
    token = _gh_token()
    now = datetime.now(timezone.utc)
    cleaned = _clean_terminal_runs(token, now)

    orphan_names = _find_orphan_repos(token, now)
    orphans_deleted = 0
    for name in orphan_names:
        if _delete_repo(name, token):
            orphans_deleted += 1
            logger.info("dogfood reconciler: deleted orphan repo %s", name)

    report = {
        "cleaned": cleaned,
        "orphans_found": len(orphan_names),
        "orphans_deleted": orphans_deleted,
    }
    if cleaned or orphan_names:
        logger.info("dogfood reconciler: %s", report)
    return report

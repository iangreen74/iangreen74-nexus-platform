"""
CI Monitor Sensor.

Reads recent GitHub Actions workflow runs for the Forgewing repos
and produces a CIHealthReport. Uses a fine-grained PAT from Secrets
Manager (GITHUB_SECRET_ID). In local mode, returns synthetic green data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from nexus.aws_client import get_secret
from nexus.config import GITHUB_ORG, GITHUB_REPOS, GITHUB_SECRET_ID, MODE

logger = logging.getLogger("nexus.sensors.ci")

GITHUB_API = "https://api.github.com"


def _token() -> str | None:
    if MODE != "production":
        return None
    secret = get_secret(GITHUB_SECRET_ID)
    # `github-token` is stored as a plain string, not JSON; aws_client wraps
    # plain strings as {"_raw": "..."}. Fall back to JSON shapes for safety.
    return (
        secret.get("_raw")
        or secret.get("github_pat")
        or secret.get("token")
    )


def _fetch_runs(repo: str, token: str) -> list[dict[str, Any]]:
    url = f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/actions/runs?per_page=50"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning("GitHub runs fetch %s => %s", repo, resp.status_code)
            return []
        return resp.json().get("workflow_runs", [])
    except Exception:
        logger.exception("_fetch_runs(%s) failed", repo)
        return []


def _mock_report() -> dict[str, Any]:
    return {
        "last_run_status": "success",
        "green_rate_24h": 0.92,
        "recent_green_rate": 1.0,
        "recent_run_count": 10,
        "failing_workflows": [],
        "repos_checked": list(GITHUB_REPOS),
        "run_count": 12,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "healthy": True,
    }


def check_ci() -> dict[str, Any]:
    """Build a CIHealthReport. Never raises."""
    if MODE != "production":
        return _mock_report()

    token = _token()
    if not token:
        logger.error("No GitHub PAT available — CI monitor disabled")
        return {
            "healthy": False,
            "error": "missing_token",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    all_runs: list[dict[str, Any]] = []
    failing: set[str] = set()
    for repo in GITHUB_REPOS:
        all_runs.extend(_fetch_runs(repo, token))

    if not all_runs:
        return {
            "healthy": False,
            "last_run_status": "unknown",
            "green_rate_24h": 0.0,
            "failing_workflows": [],
            "repos_checked": list(GITHUB_REPOS),
            "run_count": 0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    total = len(all_runs)
    green = 0
    for run in all_runs:
        conclusion = run.get("conclusion")
        if conclusion == "success":
            green += 1
        elif conclusion == "failure":
            name = run.get("name") or run.get("workflow_id")
            if name:
                failing.add(str(name))
    latest = all_runs[0]
    last_status = latest.get("status") or latest.get("conclusion") or "unknown"

    # Recent-10 trend. After a spate of failures the 24h window stays red
    # until old runs age out, but the *last few* runs tell us whether the
    # underlying issue is fixed. Sorted newest-first by _fetch_runs.
    recent = sorted(
        all_runs,
        key=lambda r: r.get("created_at") or r.get("run_started_at") or "",
        reverse=True,
    )[:10]
    recent_total = len(recent)
    recent_green = sum(1 for r in recent if r.get("conclusion") == "success")
    recent_rate = round(recent_green / recent_total, 3) if recent_total else 0.0

    return {
        "last_run_status": last_status,
        "green_rate_24h": round(green / total, 3),
        "recent_green_rate": recent_rate,
        "recent_run_count": recent_total,
        "failing_workflows": sorted(failing),
        "repos_checked": list(GITHUB_REPOS),
        "run_count": total,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "healthy": last_status == "success" and not failing,
    }

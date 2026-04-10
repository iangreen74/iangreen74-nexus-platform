"""
CI Operations — GitHub Actions monitoring and intervention.

Capabilities for diagnosing failing workflows and retriggering
transient failures. All operations use the shared GitHub PAT.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from nexus.aws_client import get_secret
from nexus.capabilities.registry import Capability, registry
from nexus.config import (
    ARIA_PLATFORM_REPO,
    BLAST_MODERATE,
    BLAST_SAFE,
    GITHUB_SECRET_ID,
    MODE,
)

logger = logging.getLogger("nexus.capabilities.ci_ops")

GITHUB_API = "https://api.github.com"


def _headers() -> dict[str, str]:
    token = get_secret(GITHUB_SECRET_ID).get("_raw")
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }


def get_failing_workflows(**_: Any) -> dict[str, Any]:
    """
    List GitHub Actions workflows that failed in the last 24h.

    For each failure: captures the failing step, error message, and run URL.
    Safe blast radius — read-only.
    """
    if MODE != "production":
        return {
            "mock": True,
            "failing": [
                {"workflow": "Daemon CI/CD", "step": "Run tests", "message": "exit code 1", "url": ""},
            ],
        }
    try:
        resp = httpx.get(
            f"{GITHUB_API}/repos/{ARIA_PLATFORM_REPO}/actions/runs",
            params={"status": "failure", "per_page": 20},
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "status": resp.status_code}
        runs = resp.json().get("workflow_runs", [])
        failing: list[dict[str, Any]] = []
        for run in runs[:10]:
            # Get the failing job for this run
            jobs_url = run.get("jobs_url", "")
            job_info = ""
            if jobs_url:
                try:
                    jobs_resp = httpx.get(jobs_url, headers=_headers(), timeout=10)
                    if jobs_resp.status_code == 200:
                        for job in jobs_resp.json().get("jobs", []):
                            if job.get("conclusion") == "failure":
                                for step in job.get("steps", []):
                                    if step.get("conclusion") == "failure":
                                        job_info = step.get("name", "")
                                        break
                                break
                except Exception:
                    pass
            failing.append({
                "workflow": run.get("name"),
                "run_id": run.get("id"),
                "step": job_info or "unknown",
                "url": run.get("html_url"),
                "created_at": run.get("created_at"),
            })
        return {"failing": failing, "count": len(failing)}
    except Exception as exc:
        logger.exception("get_failing_workflows failed")
        return {"error": str(exc)}


def retrigger_workflow(run_id: int = 0, **_: Any) -> dict[str, Any]:
    """
    Re-run a failed workflow by run_id. Used for transient failures.

    Moderate blast radius — triggers a new CI run that consumes runner minutes.
    """
    if not run_id:
        return {"error": "run_id is required"}
    if MODE != "production":
        return {"mock": True, "rerun": True, "run_id": run_id}
    try:
        resp = httpx.post(
            f"{GITHUB_API}/repos/{ARIA_PLATFORM_REPO}/actions/runs/{run_id}/rerun",
            headers=_headers(),
            timeout=15,
        )
        return {
            "rerun": resp.status_code == 201,
            "run_id": run_id,
            "status": resp.status_code,
        }
    except Exception as exc:
        logger.exception("retrigger_workflow failed for run %s", run_id)
        return {"error": str(exc), "run_id": run_id}


registry.register(Capability(
    name="get_failing_workflows",
    function=get_failing_workflows,
    blast_radius=BLAST_SAFE,
    description="List GitHub Actions failures with step detail + run URLs",
))
registry.register(Capability(
    name="retrigger_workflow",
    function=retrigger_workflow,
    blast_radius=BLAST_MODERATE,
    description="Re-run a failed GitHub Actions workflow (transient failures)",
    requires_approval=False,
))

"""
CI Heartbeat — detects hung GitHub Actions jobs before they cost hours.

2026-04-14 outage: Playwright apt-get deadlocked on dpkg lock; e2e-tests
ran 22min with no signal; 5 hours of manual diagnosis. This scans
in-progress jobs every cycle and records a CIIncident for anything past
a per-job budget. Downstream: ci_healer for remediation, ci_patterns
for learning.

NOTE (Forgewing Cap 23 — future): same thresholds will apply to customer
pipelines via the vaultscaler-pr-gateway App.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from nexus import overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_SAFE, GITHUB_ORG, GITHUB_REPOS, GITHUB_SECRET_ID, MODE
from nexus.aws_client import get_secret

logger = logging.getLogger("nexus.capabilities.ci_heartbeat")

GITHUB_API = "https://api.github.com"

# Per-job elapsed-time budgets in seconds. Jobs routinely finish well
# inside these numbers on a healthy runner; anything past the budget is
# treated as a hang. Tuned to the incident on 2026-04-14.
_JOB_BUDGETS: dict[str, int] = {
    "invariant-checks": 120,
    "test": 300,
    "e2e-tests": 480,
    "build-image": 300,
    "smoke-test": 180,
    "test-staging": 480,
}
_DEFAULT_BUDGET_SEC = 900


def _token() -> str | None:
    if MODE != "production":
        return None
    secret = get_secret(GITHUB_SECRET_ID) or {}
    return secret.get("_raw") or secret.get("github_pat") or secret.get("token")


def _gh_get(url: str, token: str) -> dict[str, Any] | list[Any] | None:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
    except Exception:
        logger.exception("ci_heartbeat GET %s failed", url)
        return None
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        logger.warning("ci_heartbeat rate-limited on %s", url)
        return None
    if resp.status_code != 200:
        logger.warning("ci_heartbeat GET %s => %s", url, resp.status_code)
        return None
    try:
        return resp.json()
    except Exception:
        return None


def _budget_for(job_name: str) -> int:
    # Iterate longest key first so "e2e-tests" wins over "test" on substrings.
    name = (job_name or "").lower()
    for key in sorted(_JOB_BUDGETS, key=len, reverse=True):
        if key in name:
            return _JOB_BUDGETS[key]
    return _DEFAULT_BUDGET_SEC


def _elapsed_sec(started_at: str | None) -> int:
    if not started_at:
        return 0
    try:
        dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int((datetime.now(timezone.utc) - dt).total_seconds())


def _record_incident(repo: str, run: dict[str, Any], job: dict[str, Any],
                      elapsed_sec: int, budget_sec: int) -> str:
    details = {
        "repo": repo,
        "run_id": run.get("id"),
        "run_url": run.get("html_url"),
        "commit": (run.get("head_sha") or "")[:12],
        "job_id": job.get("id"),
        "job_name": job.get("name"),
        "runner_name": job.get("runner_name"),
        "current_step": _current_step(job),
        "elapsed_sec": elapsed_sec,
        "budget_sec": budget_sec,
    }
    try:
        return overwatch_graph.record_event(
            event_type="ci_hung",
            service=f"github-actions:{repo}",
            details=details,
            severity="warning",
        )
    except Exception:
        logger.exception("ci_heartbeat: record_event failed")
        return ""


def _current_step(job: dict[str, Any]) -> str:
    for s in job.get("steps") or []:
        if isinstance(s, dict) and s.get("status") == "in_progress":
            return str(s.get("name") or "")
    return ""


def check_ci_heartbeat(**_: Any) -> dict[str, Any]:
    """
    Scan in-progress GitHub Actions jobs across configured repos. Returns
    a report of hung jobs (elapsed > budget). Side-effect: each hung job
    becomes a CIIncident in the Overwatch graph.
    """
    if MODE != "production":
        return {"mock": True, "checked_at": datetime.now(timezone.utc).isoformat(),
                "hung": [], "scanned_runs": 0}

    token = _token()
    if not token:
        return {"error": "missing_github_token", "hung": [], "scanned_runs": 0}

    hung: list[dict[str, Any]] = []
    scanned = 0
    for repo in GITHUB_REPOS:
        runs = _gh_get(
            f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/actions/runs"
            "?status=in_progress&per_page=20",
            token,
        )
        if not isinstance(runs, dict):
            continue
        for run in runs.get("workflow_runs") or []:
            scanned += 1
            jobs_payload = _gh_get(
                f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/actions/runs/"
                f"{run.get('id')}/jobs?per_page=50",
                token,
            )
            if not isinstance(jobs_payload, dict):
                continue
            for job in jobs_payload.get("jobs") or []:
                if job.get("status") != "in_progress":
                    continue
                elapsed = _elapsed_sec(job.get("started_at"))
                budget = _budget_for(job.get("name") or "")
                if elapsed <= budget:
                    continue
                incident_id = _record_incident(repo, run, job, elapsed, budget)
                hung.append({
                    "repo": repo,
                    "run_id": run.get("id"),
                    "job_id": job.get("id"),
                    "job_name": job.get("name"),
                    "current_step": _current_step(job),
                    "runner_name": job.get("runner_name"),
                    "commit": (run.get("head_sha") or "")[:12],
                    "elapsed_sec": elapsed,
                    "budget_sec": budget,
                    "incident_id": incident_id,
                })

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "scanned_runs": scanned,
        "hung": hung,
        "hung_count": len(hung),
    }


registry.register(Capability(
    name="check_ci_heartbeat",
    function=check_ci_heartbeat,
    blast_radius=BLAST_SAFE,
    description=(
        "Scan in-progress GitHub Actions jobs and record a CIIncident for "
        "any job past its per-type elapsed-time budget."
    ),
))

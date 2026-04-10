"""
Overwatch's interface to the Forgewing codebase.

This module allows Overwatch to read from and propose changes to
aria-platform. All write operations go through PRs — Overwatch never
pushes directly to main. PRs are labeled with FORGE_PR_LABEL so the
operator can find them at a glance.

Authentication: GitHub PAT pulled from Secrets Manager (the same
`github-token` secret used by the CI monitor).
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from nexus.aws_client import get_secret
from nexus.config import (
    ARIA_PLATFORM_DEFAULT_BRANCH,
    ARIA_PLATFORM_REPO,
    FORGE_PR_LABEL,
    GITHUB_SECRET_ID,
    MODE,
)

logger = logging.getLogger("nexus.forge.aria_repo")

GITHUB_API = "https://api.github.com"


@dataclass
class FileChange:
    path: str
    new_content: str
    old_content: str | None = None  # for sanity checks; None means "no precondition"


def _token() -> str | None:
    if MODE != "production":
        return None
    secret = get_secret(GITHUB_SECRET_ID)
    return secret.get("_raw") or secret.get("github_pat") or secret.get("token")


def _headers() -> dict[str, str]:
    token = _token()
    if not token:
        return {}
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _request(method: str, path: str, **kwargs: Any) -> httpx.Response | None:
    """Issue a GitHub API request, returning None in local mode."""
    if MODE != "production":
        logger.debug("[local] aria_repo.%s %s", method, path)
        return None
    try:
        return httpx.request(method, f"{GITHUB_API}{path}", headers=_headers(), timeout=15, **kwargs)
    except Exception:
        logger.exception("aria_repo %s %s failed", method, path)
        return None


def read_file(path: str, ref: str = ARIA_PLATFORM_DEFAULT_BRANCH) -> str | None:
    """Return the contents of a file from aria-platform at a given ref."""
    if MODE != "production":
        return f"# [local mock] {path}\n"
    resp = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/contents/{path}", params={"ref": ref})
    if resp is None or resp.status_code != 200:
        return None
    body = resp.json()
    content = body.get("content", "")
    encoding = body.get("encoding")
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8", "replace")
    return content


def list_recent_commits(limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent commits on the default branch."""
    if MODE != "production":
        return [{"sha": "abc123", "message": "[local mock] last commit", "author": "ian"}]
    resp = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/commits", params={"per_page": limit})
    if resp is None or resp.status_code != 200:
        return []
    return [
        {
            "sha": c.get("sha"),
            "message": (c.get("commit") or {}).get("message", ""),
            "author": ((c.get("commit") or {}).get("author") or {}).get("name", ""),
            "url": c.get("html_url"),
        }
        for c in resp.json()
    ]


def list_open_prs() -> list[dict[str, Any]]:
    """Return open PRs on aria-platform."""
    if MODE != "production":
        return [{"number": 1, "title": "[local mock] sample PR", "user": "overwatch", "url": ""}]
    resp = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/pulls", params={"state": "open", "per_page": 50})
    if resp is None or resp.status_code != 200:
        return []
    return [
        {
            "number": p.get("number"),
            "title": p.get("title"),
            "user": (p.get("user") or {}).get("login"),
            "url": p.get("html_url"),
            "labels": [l.get("name") for l in (p.get("labels") or [])],
            "draft": p.get("draft", False),
            "created_at": p.get("created_at"),
        }
        for p in resp.json()
    ]


def list_overwatch_prs() -> list[dict[str, Any]]:
    """Return PRs Overwatch has opened (filtered by label)."""
    return [p for p in list_open_prs() if FORGE_PR_LABEL in (p.get("labels") or [])]


def get_workflow_status(workflow_name: str) -> dict[str, Any]:
    """Latest run status for a named workflow file."""
    if MODE != "production":
        return {"workflow": workflow_name, "status": "completed", "conclusion": "success"}
    resp = _request(
        "GET",
        f"/repos/{ARIA_PLATFORM_REPO}/actions/workflows/{workflow_name}/runs",
        params={"per_page": 1},
    )
    if resp is None or resp.status_code != 200:
        return {"workflow": workflow_name, "error": True}
    runs = resp.json().get("workflow_runs", [])
    if not runs:
        return {"workflow": workflow_name, "status": "unknown"}
    run = runs[0]
    return {
        "workflow": workflow_name,
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "url": run.get("html_url"),
        "started_at": run.get("run_started_at"),
    }


def get_ci_status(pr_number: int) -> dict[str, Any]:
    """Return the combined check status for a PR."""
    if MODE != "production":
        return {"pr": pr_number, "state": "success"}
    resp = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/pulls/{pr_number}")
    if resp is None or resp.status_code != 200:
        return {"pr": pr_number, "error": True}
    sha = (resp.json().get("head") or {}).get("sha")
    if not sha:
        return {"pr": pr_number, "state": "unknown"}
    status_resp = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/commits/{sha}/status")
    if status_resp is None or status_resp.status_code != 200:
        return {"pr": pr_number, "state": "unknown"}
    return {"pr": pr_number, "state": status_resp.json().get("state", "unknown"), "sha": sha}


def create_fix_pr(
    branch_name: str,
    file_changes: list[FileChange],
    title: str,
    body: str,
) -> dict[str, Any]:
    """
    Create a branch off main, commit the file changes, and open a PR
    labeled `overwatch-fix`. Returns {url, number} on success or
    {error: ...} on failure.

    In local mode, returns a mock result so callers can be tested without
    making real GitHub API calls.
    """
    if MODE != "production":
        return {
            "url": f"https://github.com/{ARIA_PLATFORM_REPO}/pull/MOCK",
            "number": 0,
            "branch": branch_name,
            "files": [c.path for c in file_changes],
            "mock": True,
        }

    # 1) Get base sha
    base = _request("GET", f"/repos/{ARIA_PLATFORM_REPO}/git/refs/heads/{ARIA_PLATFORM_DEFAULT_BRANCH}")
    if base is None or base.status_code != 200:
        return {"error": "could_not_resolve_base"}
    base_sha = (base.json().get("object") or {}).get("sha")

    # 2) Create branch
    create_branch = _request(
        "POST",
        f"/repos/{ARIA_PLATFORM_REPO}/git/refs",
        json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
    )
    if create_branch is None or create_branch.status_code not in (200, 201):
        return {"error": "could_not_create_branch", "status": getattr(create_branch, "status_code", None)}

    # 3) For each file: PUT contents on the new branch
    for change in file_changes:
        existing = _request(
            "GET",
            f"/repos/{ARIA_PLATFORM_REPO}/contents/{change.path}",
            params={"ref": branch_name},
        )
        sha = None
        if existing and existing.status_code == 200:
            sha = existing.json().get("sha")
        put_payload: dict[str, Any] = {
            "message": f"overwatch: {change.path}",
            "content": base64.b64encode(change.new_content.encode("utf-8")).decode("ascii"),
            "branch": branch_name,
        }
        if sha:
            put_payload["sha"] = sha
        put = _request("PUT", f"/repos/{ARIA_PLATFORM_REPO}/contents/{change.path}", json=put_payload)
        if put is None or put.status_code not in (200, 201):
            return {"error": "could_not_commit_file", "path": change.path}

    # 4) Open PR
    pr = _request(
        "POST",
        f"/repos/{ARIA_PLATFORM_REPO}/pulls",
        json={"title": title, "head": branch_name, "base": ARIA_PLATFORM_DEFAULT_BRANCH, "body": body},
    )
    if pr is None or pr.status_code not in (200, 201):
        return {"error": "could_not_open_pr"}
    pr_data = pr.json()

    # 5) Label as overwatch-fix
    _request(
        "POST",
        f"/repos/{ARIA_PLATFORM_REPO}/issues/{pr_data['number']}/labels",
        json={"labels": [FORGE_PR_LABEL]},
    )

    return {"url": pr_data.get("html_url"), "number": pr_data.get("number"), "branch": branch_name}

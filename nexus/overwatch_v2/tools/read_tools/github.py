"""Tool 3 — read_github: file/PR/commits/workflow-runs reads via GitHub REST.

Repo enum restricts to two known repos so the reasoner cannot accidentally
read random repos. Auth via the overwatch-v2-reasoner GitHub App; the
installation token is minted on demand and cached in-process by
_github_app_auth (credentials in overwatch-v2/github-app).
"""
from __future__ import annotations

import base64
from typing import Any

import httpx

from nexus.overwatch_v2.tools.read_tools._github_app_auth import (
    get_installation_token,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown,
)


GITHUB_API = "https://api.github.com"
ALLOWED_REPOS = ["iangreen74/aria-platform", "iangreen74/iangreen74-nexus-platform"]

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {
            "type": "string",
            "enum": ["read_file", "read_pr", "list_commits", "list_workflow_runs"],
        },
        "repo": {"type": "string", "enum": ALLOWED_REPOS,
                 "description": "owner/name; restricted to the V2-managed repos."},
        "path": {"type": "string", "description": "file path (read_file)"},
        "ref": {"type": "string",
                "description": "branch/tag/sha (read_file, list_commits)"},
        "pr_number": {"type": "integer", "description": "for read_pr"},
        "limit": {"type": "integer", "description": "default 20, max 100"},
    },
    "required": ["operation", "repo"],
}


def _headers() -> dict:
    try:
        token = get_installation_token()
    except Exception as e:
        raise ToolUnknown(f"github auth failed: {e}") from e
    return {"Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _check_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise ToolForbidden(f"github {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 404:
        raise ToolNotFound(f"github 404: {resp.text[:200]}")
    if resp.status_code == 429:
        raise ToolThrottled(f"github 429: {resp.text[:200]}")
    raise ToolUnknown(f"github {resp.status_code}: {resp.text[:200]}")


def handler(**params: Any) -> dict:
    op = params["operation"]
    repo = params["repo"]
    if repo not in ALLOWED_REPOS:
        raise ToolUnknown(f"repo {repo!r} not in ALLOWED_REPOS")
    limit = max(1, min(int(params.get("limit") or 20), 100))
    if op == "read_file":
        path = params.get("path")
        if not path:
            raise ToolUnknown("read_file requires `path`")
        ref = params.get("ref") or "main"
        url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
        with httpx.Client(timeout=15) as c:
            r = c.get(url, params={"ref": ref}, headers=_headers())
        _check_status(r)
        body = r.json()
        content = body.get("content", "")
        try:
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            decoded = ""
        return {"path": body.get("path", path), "sha": body.get("sha"),
                "size": body.get("size", 0), "content": decoded[:64_000]}
    if op == "read_pr":
        pr = params.get("pr_number")
        if not pr:
            raise ToolUnknown("read_pr requires `pr_number`")
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr}", headers=_headers())
        _check_status(r)
        b = r.json()
        return {"title": b.get("title"), "body": (b.get("body") or "")[:8000],
                "state": b.get("state"), "merged": b.get("merged"),
                "head_ref": (b.get("head") or {}).get("ref"),
                "base_ref": (b.get("base") or {}).get("ref")}
    if op == "list_commits":
        ref = params.get("ref")
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{GITHUB_API}/repos/{repo}/commits",
                      params={"sha": ref, "per_page": limit} if ref else {"per_page": limit},
                      headers=_headers())
        _check_status(r)
        return {"commits": [
            {"sha": c["sha"], "message": (c.get("commit") or {}).get("message", "")[:500],
             "author": ((c.get("commit") or {}).get("author") or {}).get("name"),
             "date": ((c.get("commit") or {}).get("author") or {}).get("date")}
            for c in r.json()
        ]}
    if op == "list_workflow_runs":
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{GITHUB_API}/repos/{repo}/actions/runs",
                      params={"per_page": limit}, headers=_headers())
        _check_status(r)
        runs = (r.json() or {}).get("workflow_runs", [])
        return {"workflow_runs": [
            {"id": rr.get("id"), "name": rr.get("name"),
             "status": rr.get("status"), "conclusion": rr.get("conclusion"),
             "created_at": rr.get("created_at"), "html_url": rr.get("html_url")}
            for rr in runs
        ]}
    raise ToolUnknown(f"unknown operation: {op!r}")


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_github",
        description=(
            "Read files, PRs, commits, or workflow runs from a V2-managed "
            "GitHub repo. Repo restricted to: " + ", ".join(ALLOWED_REPOS)
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

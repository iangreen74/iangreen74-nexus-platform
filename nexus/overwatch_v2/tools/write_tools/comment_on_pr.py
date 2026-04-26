"""Tool — comment_on_pr: post a comment on a PR (mutation; approval-gated).

Phase 1's first mutation tool — proves the approval-gate substrate
end-to-end. Pure GitHub API, no AWS state, lowest blast radius.

Auth: GitHub App installation token (same path as read_github;
nexus.overwatch_v2.tools.read_tools._github_app_auth.get_installation_token).
Allowlist: same as read_github (iangreen74/aria-platform +
iangreen74/iangreen74-nexus-platform).

Approval gate: requires_approval=True. Operator must issue a token
with proposal_id=f"tool:comment_on_pr" and proposal_payload
={"tool_name": "comment_on_pr", "params": {repo, pr_number, body}}.
The dispatch-time precheck (nexus.overwatch_v2.tools._approval_gate)
verifies signature, hash binding, expiry, and single-use, then
audits the attempt to /overwatch-v2/echo-mutations.
"""
from __future__ import annotations

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
MAX_BODY_CHARS = 65536  # GitHub issue-comment max

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {
            "type": "string", "enum": ALLOWED_REPOS,
            "description": "owner/name; allowlisted to V2-managed repos.",
        },
        "pr_number": {
            "type": "integer", "description": "PR number to comment on.",
        },
        "body": {
            "type": "string",
            "description": f"Markdown comment body. Max {MAX_BODY_CHARS} chars.",
        },
    },
    "required": ["repo", "pr_number", "body"],
}


def _headers() -> dict[str, str]:
    try:
        token = get_installation_token()
    except Exception as e:
        raise ToolUnknown(f"github auth failed: {e}") from e
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _check_status(resp: httpx.Response) -> None:
    if resp.status_code in (200, 201):
        return
    if resp.status_code in (401, 403):
        raise ToolForbidden(f"github {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 404:
        raise ToolNotFound(f"github 404: {resp.text[:200]}")
    if resp.status_code == 429:
        raise ToolThrottled(f"github 429: {resp.text[:200]}")
    raise ToolUnknown(f"github {resp.status_code}: {resp.text[:200]}")


def handler(**params: Any) -> dict[str, Any]:
    repo = params["repo"]
    if repo not in ALLOWED_REPOS:
        raise ToolUnknown(f"repo {repo!r} not in ALLOWED_REPOS")
    pr_number = int(params["pr_number"])
    body = str(params["body"])
    if not body.strip():
        raise ToolUnknown("body must be non-empty")
    if len(body) > MAX_BODY_CHARS:
        raise ToolUnknown(f"body exceeds {MAX_BODY_CHARS} chars")
    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    try:
        with httpx.Client(timeout=15) as c:
            resp = c.post(url, headers=_headers(), json={"body": body})
    except httpx.HTTPError as e:
        raise ToolUnknown(f"github HTTP error: {e}") from e
    _check_status(resp)
    payload = resp.json() or {}
    return {
        "ok": True,
        "comment_id": payload.get("id"),
        "comment_url": payload.get("html_url"),
        "repo": repo,
        "pr_number": pr_number,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_MEDIUM, ToolSpec, register
    register(ToolSpec(
        name="comment_on_pr",
        description=(
            "Post a comment on a GitHub PR (allowlisted repos only). "
            "MUTATION — requires approval_token. Operator UI must issue a "
            "token bound to proposal_id='tool:comment_on_pr' and a "
            "proposal_payload of {tool_name, params}; dispatch verifies "
            "signature + hash + expiry + single-use before invoking the "
            "handler. Every attempt audited to /overwatch-v2/echo-mutations."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=True,
        risk_level=RISK_MEDIUM,
    ))

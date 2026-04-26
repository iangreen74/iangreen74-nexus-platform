"""Tool 10 — read_git_diff: per-commit diffs from an allowlisted repo.

Phase 0a / Layer 1, source #1. Backed by GET /repos/{owner}/{repo}/commits/{sha}
which returns the commit metadata plus a `files` array with each changed file's
patch text. When `file` is supplied we filter to just that path; otherwise we
return all changed files.

The patch text per file is GitHub-truncated at ~500KB; we cap it again at
50,000 chars per file to keep the reasoner's context window sane. Callers
needing the full diff should fetch `download_url` separately.
"""
from __future__ import annotations

from typing import Any

import httpx

from nexus.overwatch_v2.tools.read_tools._github_app_auth import (
    get_installation_token,
)
from nexus.overwatch_v2.tools.read_tools._repo_allowlist import (
    ALLOWED_REPOS, assert_repo_allowed,
)
from nexus.overwatch_v2.tools.read_tools.exceptions import (
    ToolForbidden, ToolNotFound, ToolThrottled, ToolUnknown,
)


GITHUB_API = "https://api.github.com"

MAX_PATCH_CHARS = 50_000

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "enum": sorted(ALLOWED_REPOS),
                 "description": "owner/name; restricted to the codebase-indexing allowlist."},
        "sha": {"type": "string",
                "description": "Full or short commit SHA to read."},
        "file": {"type": "string",
                 "description": "Optional. If set, returns the diff for only this path."},
    },
    "required": ["repo", "sha"],
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
    if resp.status_code == 422:
        raise ToolUnknown(f"github 422: {resp.text[:200]}")
    if resp.status_code == 429:
        raise ToolThrottled(f"github 429: {resp.text[:200]}")
    raise ToolUnknown(f"github {resp.status_code}: {resp.text[:200]}")


def _normalize_file(f: dict) -> dict:
    patch = f.get("patch") or ""
    truncated = len(patch) > MAX_PATCH_CHARS
    return {
        "path": f.get("filename"),
        "status": f.get("status"),
        "additions": int(f.get("additions") or 0),
        "deletions": int(f.get("deletions") or 0),
        "changes": int(f.get("changes") or 0),
        "patch": patch[:MAX_PATCH_CHARS],
        "patch_truncated": truncated,
    }


def handler(**params: Any) -> dict:
    repo = params["repo"]
    sha = params["sha"]
    file_filter = params.get("file") or None
    assert_repo_allowed(repo)

    url = f"{GITHUB_API}/repos/{repo}/commits/{sha}"
    with httpx.Client(timeout=15) as client:
        r = client.get(url, headers=_headers())
        _check_status(r)
        body = r.json() or {}

    commit = body.get("commit") or {}
    author = commit.get("author") or {}
    files = body.get("files") or []
    if file_filter:
        files = [f for f in files if (f.get("filename") == file_filter)]

    diffs = [_normalize_file(f) for f in files]
    stats = body.get("stats") or {}

    return {
        "repo": repo,
        "sha": body.get("sha") or sha,
        "html_url": body.get("html_url"),
        "message": (commit.get("message") or "")[:2000],
        "author": author.get("name"),
        "date": author.get("date"),
        "files_changed": len(diffs),
        "additions": int(stats.get("additions") or 0),
        "deletions": int(stats.get("deletions") or 0),
        "diffs": diffs,
        "filtered_to_file": file_filter,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_git_diff",
        description=(
            "Read the patch / diff for a specific commit in an allowlisted repo. "
            "Optionally scope to a single file. Use to answer 'what changed in "
            "commit X' or 'how did file Y change'. Patches are capped at "
            f"{MAX_PATCH_CHARS} chars per file."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

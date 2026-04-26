"""Tool 11 — list_repo_files: directory listing for an allowlisted repo.

Phase 0a / Layer 1, source #1. Backed by GET /repos/{owner}/{repo}/contents/{path}
which returns an array of entries when `path` is a directory. Use to explore
repo layout before drilling into a specific file with read_repo_file.
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

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "enum": sorted(ALLOWED_REPOS),
                 "description": "owner/name; restricted to the codebase-indexing allowlist."},
        "path": {"type": "string",
                 "description": "Directory path within the repo. Empty string = repo root."},
        "ref": {"type": "string",
                "description": "branch, tag, or commit SHA. Defaults to 'main'."},
    },
    "required": ["repo"],
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
    repo = params["repo"]
    path = params.get("path") or ""
    ref = params.get("ref") or "main"
    assert_repo_allowed(repo)

    url = f"{GITHUB_API}/repos/{repo}/contents/{path}" if path else \
          f"{GITHUB_API}/repos/{repo}/contents/"
    with httpx.Client(timeout=15) as client:
        r = client.get(url, params={"ref": ref}, headers=_headers())
        _check_status(r)
        body = r.json()

    if isinstance(body, dict):
        # path resolved to a single file, not a directory.
        raise ToolUnknown(
            f"path {path!r} is a file; use read_repo_file instead."
        )

    entries = []
    for it in body or []:
        entries.append({
            "name": it.get("name"),
            "path": it.get("path"),
            "type": it.get("type"),    # "file" | "dir" | "symlink" | "submodule"
            "size": int(it.get("size") or 0),
            "sha": it.get("sha"),
        })
    # Stable order: dirs first, then files, alphabetical within each.
    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"] or ""))

    return {
        "repo": repo,
        "path": path,
        "ref": ref,
        "entry_count": len(entries),
        "entries": entries,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="list_repo_files",
        description=(
            "List directory contents in an allowlisted repo. Use to explore "
            "repo layout before drilling into a file with read_repo_file. "
            "Returns name, type (file|dir|symlink|submodule), and size."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

"""Tool 8 — read_repo_file: file content + metadata from an allowlisted repo.

Phase 0a / Layer 1, source #1 of the Operational Truth Substrate. Lets Echo
ground answers in actual repo content with file:line citations.

Two-call shape: GET /contents for the file body + sha + size, then a second
GET /commits?path= for last_modified / last_modified_by. The second call is
best-effort — if it fails the metadata fields come back as None rather than
failing the whole read.

GitHub's contents API silently truncates files between 1MB and 100MB
(returns content="" with `truncated=true`); files >100MB error. We surface
that 1MB threshold as a warning and add a hard 10MB refusal of our own.
"""
from __future__ import annotations

import base64
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

# Ext → human language label. Mostly informational for the reasoner.
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".java": "java",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json", ".toml": "toml",
    ".md": "markdown", ".html": "html", ".css": "css", ".scss": "scss",
    ".sql": "sql", ".tf": "terraform", ".dockerfile": "dockerfile",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".php": "php", ".swift": "swift", ".kt": "kotlin",
}

WARN_BYTES = 1 * 1024 * 1024       # 1 MB — warning, content may be truncated
REFUSE_BYTES = 10 * 1024 * 1024    # 10 MB — outright refuse

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "enum": sorted(ALLOWED_REPOS),
                 "description": "owner/name; restricted to the codebase-indexing allowlist."},
        "path": {"type": "string",
                 "description": "file path within the repo (e.g., 'nexus/server.py')."},
        "ref": {"type": "string",
                "description": "branch, tag, or commit SHA. Defaults to 'main'."},
    },
    "required": ["repo", "path"],
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


def _language_for(path: str) -> str:
    lower = path.lower()
    for ext, lang in _LANG_BY_EXT.items():
        if lower.endswith(ext):
            return lang
    return "unknown"


def _last_commit_for_path(client: httpx.Client, repo: str, path: str,
                          ref: str) -> tuple[str | None, str | None]:
    """Best-effort fetch of (last_modified ISO, last_modified_by login).

    Returns (None, None) if the call fails — caller should not propagate.
    """
    try:
        r = client.get(
            f"{GITHUB_API}/repos/{repo}/commits",
            params={"path": path, "sha": ref, "per_page": 1},
            headers=_headers(),
        )
    except Exception:
        return None, None
    if r.status_code != 200:
        return None, None
    items = r.json() or []
    if not items:
        return None, None
    first = items[0]
    commit = first.get("commit") or {}
    author = commit.get("author") or {}
    user = first.get("author") or {}
    return author.get("date"), user.get("login")


def handler(**params: Any) -> dict:
    repo = params["repo"]
    path = params["path"]
    ref = params.get("ref") or "main"
    assert_repo_allowed(repo)

    url = f"{GITHUB_API}/repos/{repo}/contents/{path}"
    with httpx.Client(timeout=15) as client:
        r = client.get(url, params={"ref": ref}, headers=_headers())
        _check_status(r)
        body = r.json()

        # Defensive: contents API returns a list when `path` is a directory.
        if isinstance(body, list):
            raise ToolUnknown(
                f"path {path!r} is a directory; use list_repo_files instead."
            )

        size = int(body.get("size") or 0)
        if size > REFUSE_BYTES:
            raise ToolUnknown(
                f"file too large: {size} bytes > {REFUSE_BYTES} byte limit"
            )

        encoding = body.get("encoding")
        raw = body.get("content") or ""
        truncated = bool(body.get("truncated"))
        warning: str | None = None
        content = ""
        if encoding == "base64" and raw:
            try:
                content = base64.b64decode(raw).decode("utf-8", errors="replace")
            except Exception as e:
                raise ToolUnknown(f"base64 decode failed for {path}: {e}") from e
        elif size > WARN_BYTES:
            # API stripped content because file is between 1MB and 100MB.
            truncated = True
        if truncated or size > WARN_BYTES:
            warning = (
                f"file is {size} bytes (>{WARN_BYTES}); GitHub returns "
                f"truncated/empty content above this threshold"
            )

        last_modified, last_modified_by = _last_commit_for_path(
            client, repo, path, ref,
        )

    return {
        "repo": repo,
        "path": body.get("path") or path,
        "ref": ref,
        "sha": body.get("sha"),
        "content": content,
        "size_bytes": size,
        "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        "language": _language_for(body.get("path") or path),
        "truncated": truncated,
        "warning": warning,
        "last_modified": last_modified,
        "last_modified_by": last_modified_by,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_repo_file",
        description=(
            "Read a single source file from an allowlisted repo with metadata "
            "(sha, language, last_modified, last_modified_by). Use to ground "
            "answers about code in actual file content. Files >1MB return a "
            "truncation warning; >10MB are refused. Repos: "
            + ", ".join(sorted(ALLOWED_REPOS))
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

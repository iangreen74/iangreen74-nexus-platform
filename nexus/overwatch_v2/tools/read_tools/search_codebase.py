"""Tool 9 — search_codebase: full-text search across allowlisted repos.

Phase 0a / Layer 1, source #1. Backed by GitHub Code Search API
(GET /search/code). When `repo` is omitted we union both allowlisted
repos by sending one search per repo and merging results.

GitHub Code Search caveats we live with at Phase 0a:
- Default branch only.
- Authenticated rate limit: 30 req/min.
- Returned matches don't include line numbers; we surface fragment text
  as `context` and leave `line` null when we can't compute it.

Match types we surface:
- "filename": query appears in the file path/basename.
- "content":  query matched inside the file body.
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

DEFAULT_MAX = 20
HARD_MAX = 100   # GitHub Search API caps per_page at 100

PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "Free-text search; supports GitHub code-search syntax."},
        "repo": {"type": "string", "enum": sorted(ALLOWED_REPOS),
                 "description": "Optional. If omitted, searches both allowlisted repos."},
        "max_results": {"type": "integer",
                        "description": f"Default {DEFAULT_MAX}, hard cap {HARD_MAX}."},
    },
    "required": ["query"],
}


def _headers() -> dict:
    try:
        token = get_installation_token()
    except Exception as e:
        raise ToolUnknown(f"github auth failed: {e}") from e
    return {"Authorization": f"Bearer {token}",
            # text-match preview gives us snippet fragments + indices.
            "Accept": "application/vnd.github.text-match+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def _check_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    if resp.status_code in (401, 403):
        raise ToolForbidden(f"github {resp.status_code}: {resp.text[:200]}")
    if resp.status_code == 404:
        raise ToolNotFound(f"github 404: {resp.text[:200]}")
    if resp.status_code == 422:
        # 422 = invalid query. Surface as ToolUnknown with the message.
        raise ToolUnknown(f"github 422 (invalid query): {resp.text[:200]}")
    if resp.status_code == 429:
        raise ToolThrottled(f"github 429: {resp.text[:200]}")
    raise ToolUnknown(f"github {resp.status_code}: {resp.text[:200]}")


def _classify_match(item: dict, query: str) -> str:
    name = (item.get("name") or "").lower()
    path = (item.get("path") or "").lower()
    q = query.lower()
    if q and (q in name or q in path):
        return "filename"
    return "content"


def _first_fragment(item: dict) -> str:
    matches = item.get("text_matches") or []
    if not matches:
        return ""
    fragment = (matches[0] or {}).get("fragment") or ""
    return fragment[:500]


def _search_one_repo(client: httpx.Client, query: str, repo: str,
                     per_page: int) -> tuple[list[dict], int]:
    """Returns (items, total_count) from /search/code scoped to one repo."""
    q = f"{query} repo:{repo}"
    r = client.get(
        f"{GITHUB_API}/search/code",
        params={"q": q, "per_page": per_page},
        headers=_headers(),
    )
    _check_status(r)
    body = r.json() or {}
    return body.get("items") or [], int(body.get("total_count") or 0)


def handler(**params: Any) -> dict:
    query = (params.get("query") or "").strip()
    if not query:
        raise ToolUnknown("query must be a non-empty string")
    repo = params.get("repo")
    if repo is not None:
        assert_repo_allowed(repo)

    requested = int(params.get("max_results") or DEFAULT_MAX)
    if requested < 1:
        requested = 1
    if requested > HARD_MAX:
        requested = HARD_MAX

    targets = [repo] if repo else sorted(ALLOWED_REPOS)
    # When unioning two repos, divide the per-page budget between them
    # (rounded up) so we don't exceed `requested` total after merge.
    per_page = requested if len(targets) == 1 else max(1, (requested + 1) // 2)

    results: list[dict] = []
    total = 0
    with httpx.Client(timeout=15) as client:
        for target in targets:
            items, repo_total = _search_one_repo(client, query, target, per_page)
            total += repo_total
            for it in items:
                results.append({
                    "repo": target,
                    "path": it.get("path"),
                    "line": None,  # not provided by /search/code
                    "context": _first_fragment(it),
                    "match_type": _classify_match(it, query),
                    "score": it.get("score"),
                    "html_url": it.get("html_url"),
                })

    truncated = len(results) > requested or total > len(results)
    results = results[:requested]
    return {
        "query": query,
        "repos_searched": targets,
        "results": results,
        "total_found": total,
        "truncated": truncated,
    }


def register_tool() -> None:
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="search_codebase",
        description=(
            "Full-text search across allowlisted repos via GitHub Code Search. "
            "Use to locate where a symbol/string/pattern appears. Results include "
            "repo, path, fragment context, and match_type (filename|content). "
            "Default 20 results, max 100. Searches default branch only."
        ),
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))

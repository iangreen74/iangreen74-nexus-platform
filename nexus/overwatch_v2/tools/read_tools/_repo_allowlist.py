"""Allowed repos for the codebase-indexing read tools (Phase 0a).

Echo can read source from these repos via read_repo_file, search_codebase,
read_git_diff, and list_repo_files. Other repos are refused at the handler
boundary so the reasoner cannot read arbitrary GitHub content via her tools
even if a parameter schema is widened later.
"""
from __future__ import annotations

from nexus.overwatch_v2.tools.read_tools.exceptions import ToolForbidden

ALLOWED_REPOS = frozenset({
    "iangreen74/aria-platform",
    "iangreen74/iangreen74-nexus-platform",
})


def assert_repo_allowed(repo: str) -> None:
    """Raise ToolForbidden if `repo` is not in the codebase-indexing allowlist."""
    if repo not in ALLOWED_REPOS:
        raise ToolForbidden(
            f"repo {repo!r} not in codebase-indexing allowlist. "
            f"Allowed: {sorted(ALLOWED_REPOS)}"
        )

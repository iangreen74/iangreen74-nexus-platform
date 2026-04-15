"""
Session Context — recent commit activity for the Bedrock synthesizer.

Without this, diagnosis keeps re-flagging problems that were already
fixed a few commits ago. By injecting "12 commits in the last 12 hours:
ARIA UX (8), CI/CD (3), infrastructure (1)" into the evidence block,
the synthesizer can correlate findings with recent code motion — e.g.
"brief isolation was fixed 6 hours ago (cc4e514); the project_separation
synthetic failure on the timeline is from BEFORE that fix."

Categorization is regex/keyword based on the commit subject. It isn't
perfect, but it's fast, transparent, and fails to 'other' cleanly when
a message doesn't match.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("nexus.capabilities.session_context")

_CATEGORIES: list[tuple[str, re.Pattern[str]]] = [
    ("ci_cd",           re.compile(r"\b(ci|cd|pipeline|workflow|runner|github actions|deploy)\b", re.I)),
    ("infrastructure",  re.compile(r"\b(infra|ecs|cloudformation|iam|neptune|secret|ssm|ec2)\b", re.I)),
    ("synthetic_tests", re.compile(r"\b(synthetic|journey|test gate|test_cycle)\b", re.I)),
    ("diagnosis",       re.compile(r"\b(diagnos|triage|investigation|overwatch)\b", re.I)),
    ("ux",              re.compile(r"\b(ui|ux|dashboard|aria|console|frontend|panel)\b", re.I)),
    ("brief",           re.compile(r"\b(brief|isolation|scoping|scoped)\b", re.I)),
    ("backend",         re.compile(r"\b(api|route|endpoint|handler|backend|graph)\b", re.I)),
]


def _categorize(message: str) -> str:
    first_line = (message or "").splitlines()[0] if message else ""
    for name, pattern in _CATEGORIES:
        if pattern.search(first_line):
            return name
    return "other"


def _parse_commit_ts(commit: dict[str, Any]) -> datetime | None:
    """Best-effort parse of a commit timestamp. Supports the shape from
    nexus.forge.aria_repo.list_recent_commits (which may or may not
    surface a timestamp) and the raw GitHub API response."""
    for path in (("committed_at",), ("timestamp",),
                 ("commit", "committer", "date"),
                 ("commit", "author", "date")):
        cur: Any = commit
        for key in path:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if isinstance(cur, str):
            try:
                return datetime.fromisoformat(cur.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
    return None


def gather_session_context(hours: int = 24,
                            limit: int = 30) -> dict[str, Any]:
    """
    Return {commits: [...], counts_by_category: {...}, total, window_hours}.
    Never raises — a missing GitHub token or network blip returns an empty
    context rather than breaking diagnosis.
    """
    try:
        from nexus.forge.aria_repo import list_recent_commits
    except Exception:
        return _empty(hours)

    try:
        raw = list_recent_commits(limit=limit) or []
    except Exception:
        logger.exception("session_context: list_recent_commits failed")
        return _empty(hours)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    commits: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        ts = _parse_commit_ts(c)
        if ts is not None and ts < cutoff:
            continue  # older than the window (mocks without timestamps still pass)
        category = _categorize(c.get("message", ""))
        commits.append({
            "sha": (c.get("sha") or "")[:12],
            "message_first_line": (c.get("message") or "").splitlines()[0][:120],
            "author": c.get("author"),
            "url": c.get("url"),
            "timestamp": ts.isoformat() if ts else None,
            "category": category,
        })

    counts: dict[str, int] = {}
    for c in commits:
        counts[c["category"]] = counts.get(c["category"], 0) + 1

    return {
        "commits": commits,
        "counts_by_category": counts,
        "total": len(commits),
        "window_hours": hours,
    }


def _empty(hours: int) -> dict[str, Any]:
    return {"commits": [], "counts_by_category": {}, "total": 0,
            "window_hours": hours}


def summarize_one_line(ctx: dict[str, Any]) -> str:
    """Human summary for the synthesis evidence block."""
    total = ctx.get("total", 0)
    if not total:
        return "No commits in the recent window."
    counts = ctx.get("counts_by_category") or {}
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    parts = [f"{name} ({count})" for name, count in ordered[:6]]
    return (f"{total} commits in the last {ctx.get('window_hours', '?')}h: "
            + ", ".join(parts) + ".")

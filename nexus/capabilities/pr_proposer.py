"""
PR Proposer — Overwatch opens draft PRs on aria-platform with context.

Thin wrapper on top of nexus/forge/aria_repo.create_fix_pr that enforces
the non-negotiables per CLAUDE.md Tier 3:

  1. All PRs open as drafts. Merging requires a human to flip the PR
     to ready-for-review and approve it. No auto-merge path exists.
  2. Every proposal is recorded as an OverwatchProposedPR node so the
     report's PENDING PULL REQUESTS section can surface pending work.
  3. Registered with BLAST_DANGEROUS + requires_approval=True in the
     capability registry — the registry's gating is what enforces rate
     limits and records outcomes.

Callers should already have:
  - a list[FileChange] describing the exact edits
  - a finding dict explaining what triggered the proposal (audit rule,
    consistency finding, lifecycle watchdog output, etc.)
  - a clear one-line title suitable for a PR subject

The wrapper is a single capability, not a tiered system. There is no
"Tier 1 auto-merge" here by design.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_DANGEROUS, MODE
from nexus.forge.aria_repo import FileChange, create_fix_pr

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compose_pr_body(reasoning: str, finding: dict[str, Any] | None,
                     file_paths: list[str]) -> str:
    """Render the PR description with Overwatch context."""
    import json
    parts = [
        "## Overwatch-proposed fix",
        "",
        "**Status:** Draft — a human must mark this ready for review and approve before merge.",
        "",
        "### Why",
        reasoning.strip() or "(no reasoning supplied)",
        "",
        "### Files touched",
    ]
    parts.extend(f"- `{p}`" for p in file_paths)
    parts.append("")
    if finding:
        parts.append("### Finding that triggered this")
        parts.append("```json")
        parts.append(json.dumps(finding, indent=2, default=str)[:2000])
        parts.append("```")
        parts.append("")
    parts.append("---")
    parts.append(
        "*Opened autonomously by Overwatch. No auto-merge. Review the diff "
        "against the finding above before approving.*"
    )
    return "\n".join(parts)


def propose_pr(
    tenant_id: str = "",
    *,
    branch_name: str = "",
    title: str = "",
    reasoning: str = "",
    file_changes: list[dict[str, str]] | None = None,
    finding: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """
    Propose a PR on aria-platform. Always draft, always recorded.

    Args:
      branch_name: new branch off main (e.g. "overwatch/fix-unscoped-query-1234")
      title: short PR title
      reasoning: why this fix is needed (goes in the PR body)
      file_changes: list of {"path": str, "new_content": str}
      finding: original detection payload (audit rule, watchdog, etc.)
      tenant_id: optional — tag the proposal to a tenant in the graph

    Returns a dict with status + pr_url/pr_number on success or error
    detail on failure. Never raises.
    """
    changes = file_changes or []
    if not branch_name or not title or not changes:
        return {
            "status": "error",
            "error": "branch_name, title, and file_changes are required",
        }

    fc = []
    for c in changes:
        path = (c or {}).get("path")
        content = (c or {}).get("new_content")
        if not path or content is None:
            return {"status": "error", "error": f"file_change missing path/new_content: {c}"}
        fc.append(FileChange(path=path, new_content=content))

    body = _compose_pr_body(reasoning, finding, [f.path for f in fc])

    try:
        result = create_fix_pr(
            branch_name=branch_name,
            file_changes=fc,
            title=title,
            body=body,
            draft=True,  # non-negotiable
        )
    except Exception as exc:
        logger.exception("create_fix_pr raised")
        return {"status": "error", "error": f"create_fix_pr raised: {exc}"}

    if result.get("error"):
        return {"status": "error", "error": result["error"], "detail": result}

    record = {
        "status": "proposed" if MODE == "production" else "mock",
        "draft": result.get("draft", True),
        "pr_url": result.get("url", ""),
        "pr_number": result.get("number", 0),
        "branch": result.get("branch", branch_name),
        "title": title,
        "reasoning": reasoning,
        "tenant_id": tenant_id,
        "files_changed": [f.path for f in fc],
        "finding_summary": (finding or {}).get("rule") or (finding or {}).get("check") or "",
        "created_at": _now_iso(),
    }
    _record_proposal(record)
    return record


def _record_proposal(record: dict[str, Any]) -> None:
    """Persist the proposal as an OverwatchProposedPR node for the report."""
    try:
        import json

        overwatch_graph._create_node(
            "OverwatchProposedPR",
            {
                **{k: v for k, v in record.items() if k != "files_changed"},
                "files_changed": json.dumps(record.get("files_changed", [])),
            },
        )
    except Exception:
        logger.debug("could not record OverwatchProposedPR node", exc_info=True)


def get_pending_proposals(limit: int = 20) -> list[dict[str, Any]]:
    """Return recently-proposed PRs for the report section."""
    try:
        if MODE != "production":
            rows = list(overwatch_graph._local_store.get("OverwatchProposedPR", []))
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return rows[:limit]
        return overwatch_graph.query(
            "MATCH (p:OverwatchProposedPR) "
            "RETURN p.pr_number AS pr_number, p.pr_url AS pr_url, "
            "p.title AS title, p.branch AS branch, p.status AS status, "
            "p.draft AS draft, p.reasoning AS reasoning, "
            "p.finding_summary AS finding_summary, "
            "p.tenant_id AS tenant_id, p.created_at AS created_at "
            "ORDER BY p.created_at DESC LIMIT $lim",
            {"lim": limit},
        )
    except Exception:
        logger.debug("get_pending_proposals failed", exc_info=True)
        return []


def format_for_report() -> str:
    """Render pending proposals for the diagnostic report."""
    rows = get_pending_proposals(limit=10)
    if not rows:
        return "PENDING PULL REQUESTS: none"
    lines = [f"PENDING PULL REQUESTS: {len(rows)} draft proposal(s)"]
    for r in rows:
        num = r.get("pr_number") or "?"
        url = r.get("pr_url") or ""
        title = r.get("title") or "(untitled)"
        reason = (r.get("reasoning") or "").replace("\n", " ")[:120]
        origin = r.get("finding_summary") or ""
        origin_str = f" [{origin}]" if origin else ""
        lines.append(f"  #{num}{origin_str} {title}")
        if reason:
            lines.append(f"    {reason}")
        if url:
            lines.append(f"    {url}")
    return "\n".join(lines)


# Capability registration — single entry point, dangerous blast radius,
# always requires operator approval.
registry.register(Capability(
    name="propose_pr",
    function=propose_pr,
    blast_radius=BLAST_DANGEROUS,
    description="Open a draft PR on aria-platform. Draft, labeled overwatch-fix, never auto-merged.",
    requires_approval=True,
))

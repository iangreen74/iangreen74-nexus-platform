"""
Fix Advisor — Overwatch reports findings with suggested fixes, never writes code.

Originally `propose_pr` opened draft PRs on aria-platform. That crossed the
line from "seeing" to "building" — contrary to CLAUDE.md's principle that
Overwatch is the external control plane. This module now reports only:

  1. Detection logic stays: audit rules / watchdogs / consistency checks
     produce findings as before.
  2. Suggested-fix metadata stays: file paths + reasoning + suggested_diff
     explain what the fix WOULD be.
  3. No GitHub API call. No branch creation. No draft PR.
  4. Every proposal is stored as OverwatchSuggestedFix in the graph for
     the SUGGESTED FIXES section and the investigation panel.

The PR Proposer is now the Fix Advisor — Overwatch sees, does not build.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_SAFE, MODE

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    """Record a suggested fix. No GitHub API call is made.

    Args:
      branch_name: kept for compatibility — used only in the record
      title: short title for the suggestion
      reasoning: why this fix is needed
      file_changes: list of {"path": str, "new_content": str}
      finding: original detection payload that triggered the suggestion
      tenant_id: optional — tag the suggestion to a tenant

    Returns a dict describing the recorded suggestion. Never raises.
    Never calls GitHub. Never creates branches. Never opens PRs.
    """
    changes = file_changes or []
    if not title or not changes:
        return {"status": "error",
                "error": "title and file_changes are required"}

    for c in changes:
        if not (c or {}).get("path") or (c or {}).get("new_content") is None:
            return {"status": "error",
                    "error": f"file_change missing path/new_content: {c}"}

    file_paths = [c["path"] for c in changes]
    record = {
        "status": "reported",
        "title": title,
        "reasoning": reasoning,
        "branch_suggestion": branch_name,  # advisory — nobody creates a branch
        "tenant_id": tenant_id,
        "files_touched": file_paths,
        "suggested_diff": _compose_diff_summary(changes),
        "finding_summary": (finding or {}).get("rule") or (finding or {}).get("check") or "",
        "finding": finding or {},
        "created_at": _now_iso(),
    }
    _record_suggestion(record)
    logger.info("Recorded suggested fix: %s (files=%s)", title, file_paths)
    return record


def _compose_diff_summary(changes: list[dict[str, str]]) -> str:
    """Summarize the proposed edits without storing full file contents."""
    parts = []
    for c in changes:
        path = c.get("path", "?")
        new_content = c.get("new_content", "")
        lines = new_content.count("\n") + 1 if new_content else 0
        parts.append(f"{path} ({lines} lines)")
    return "; ".join(parts)


def _record_suggestion(record: dict[str, Any]) -> None:
    """Persist as OverwatchSuggestedFix. Never raises."""
    try:
        overwatch_graph._create_node(
            "OverwatchSuggestedFix",
            {
                **{k: v for k, v in record.items()
                   if k not in ("files_touched", "finding")},
                "files_touched": json.dumps(record.get("files_touched", [])),
                "finding": json.dumps(record.get("finding", {}), default=str)[:4000],
            },
        )
    except Exception:
        logger.debug("could not record OverwatchSuggestedFix", exc_info=True)


def get_pending_suggestions(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent suggested fixes for the report + investigation panel."""
    try:
        if MODE != "production":
            rows = list(overwatch_graph._local_store.get("OverwatchSuggestedFix", []))
            rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            return rows[:limit]
        return overwatch_graph.query(
            "MATCH (p:OverwatchSuggestedFix) "
            "RETURN p.title AS title, p.reasoning AS reasoning, "
            "p.branch_suggestion AS branch_suggestion, "
            "p.files_touched AS files_touched, "
            "p.suggested_diff AS suggested_diff, "
            "p.finding_summary AS finding_summary, "
            "p.tenant_id AS tenant_id, p.created_at AS created_at "
            "ORDER BY p.created_at DESC LIMIT $lim",
            {"lim": limit},
        )
    except Exception:
        logger.debug("get_pending_suggestions failed", exc_info=True)
        return []


# Backwards-compat alias — existing callers still work
def get_pending_proposals(limit: int = 20) -> list[dict[str, Any]]:
    return get_pending_suggestions(limit)


def format_for_report() -> str:
    """Render pending suggestions for the diagnostic report."""
    rows = get_pending_suggestions(limit=10)
    if not rows:
        return "SUGGESTED FIXES: none"
    lines = [f"SUGGESTED FIXES: {len(rows)} pending"]
    for r in rows:
        title = r.get("title") or "(untitled)"
        origin = r.get("finding_summary") or ""
        origin_str = f" [{origin}]" if origin else ""
        lines.append(f"  {title}{origin_str}")
        reason = (r.get("reasoning") or "").replace("\n", " ")[:120]
        if reason:
            lines.append(f"    {reason}")
        diff = r.get("suggested_diff") or ""
        if diff:
            lines.append(f"    Touches: {diff}")
    return "\n".join(lines)


# Capability registration — now BLAST_SAFE + no approval required, since
# it only reads + writes to Overwatch's own graph. No external side effects.
registry.register(Capability(
    name="propose_pr",
    function=propose_pr,
    blast_radius=BLAST_SAFE,
    description="Report a suggested code fix. No branches, no PRs — report-only.",
    requires_approval=False,
))

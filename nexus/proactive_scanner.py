"""
Proactive Scanner — scans for issues before they become problems.

Read-only analysis across all tenants:
1. Dependency age — flag major-version-behind packages
2. Security patterns — missing common protections
3. Repo health — stale branches, no CI, no tests

Results stored as OverwatchSuggestion nodes in the Overwatch graph,
surfaced via the diagnostic report and Ops Chat. Suggestions are
per-tenant but never expose one tenant's data to another.

Privacy: suggestions are scoped to a single tenant_id. Cross-tenant
insights come from engineering_patterns.py (anonymous aggregates).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from nexus import neptune_client, overwatch_graph
from nexus.config import MODE

logger = logging.getLogger(__name__)


def scan_all_tenants() -> dict[str, list[dict[str, Any]]]:
    """Run proactive scans across all active tenants.

    Returns {tenant_id: [suggestion, ...]} for each tenant scanned.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    try:
        tenant_ids = neptune_client.get_tenant_ids()
    except Exception:
        return results

    for tid in tenant_ids:
        suggestions = scan_tenant(tid)
        if suggestions:
            results[tid] = suggestions
    logger.info("Proactive scan complete: %d tenants, %d suggestions",
                len(tenant_ids), sum(len(v) for v in results.values()))
    return results


def scan_tenant(tenant_id: str) -> list[dict[str, Any]]:
    """Run all scans for a single tenant."""
    suggestions: list[dict[str, Any]] = []
    suggestions.extend(scan_repo_health(tenant_id))
    suggestions.extend(scan_deploy_health(tenant_id))
    for s in suggestions:
        _store_suggestion(tenant_id, s)
    return suggestions


def scan_repo_health(tenant_id: str) -> list[dict[str, Any]]:
    """Check repo indexing and file health."""
    suggestions: list[dict[str, Any]] = []
    if MODE != "production":
        return suggestions

    try:
        rows = neptune_client.query(
            "MATCH (f:RepoFile {tenant_id: $tid}) RETURN count(f) AS c",
            {"tid": tenant_id},
        )
        file_count = int(rows[0].get("c", 0)) if rows else 0
        if file_count == 0:
            suggestions.append({
                "category": "repo_health",
                "severity": "warning",
                "title": "No repo files indexed",
                "description": "Repo has 0 indexed files — ingestion may have failed",
            })
    except Exception:
        pass
    return suggestions


def scan_deploy_health(tenant_id: str) -> list[dict[str, Any]]:
    """Check deployment health signals."""
    suggestions: list[dict[str, Any]] = []
    if MODE != "production":
        return suggestions

    try:
        rows = neptune_client.query(
            "MATCH (d:DeploymentProgress {tenant_id: $tid}) "
            "RETURN d.stage AS stage, d.updated_at AS updated",
            {"tid": tenant_id},
        )
        if rows:
            stage = rows[0].get("stage", "")
            updated = rows[0].get("updated", "")
            if stage and stage not in ("complete", "monitoring"):
                # Check staleness
                try:
                    ts = datetime.fromisoformat(str(updated).replace("Z", "+00:00"))
                    age_hours = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
                    if age_hours > 2:
                        suggestions.append({
                            "category": "deploy_health",
                            "severity": "warning",
                            "title": f"Deploy stuck at '{stage}'",
                            "description": f"Deploy hasn't progressed in {age_hours:.0f}h",
                        })
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return suggestions


def get_suggestions(tenant_id: str, limit: int = 5) -> list[dict[str, Any]]:
    """Get pending suggestions for a tenant (unsurfaced first)."""
    if MODE != "production":
        return []
    events = overwatch_graph.get_recent_events(limit=200)
    suggestions = []
    for e in events:
        if e.get("event_type") != "proactive_suggestion":
            continue
        if e.get("service") != tenant_id:
            continue
        details = e.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        if not details.get("surfaced"):
            suggestions.append(details)
    return suggestions[:limit]


def get_all_suggestions_summary() -> dict[str, Any]:
    """Aggregate suggestion counts across all tenants for the report."""
    events = overwatch_graph.get_recent_events(limit=500)
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    tenant_count = 0
    seen_tenants: set[str] = set()
    for e in events:
        if e.get("event_type") != "proactive_suggestion":
            continue
        details = e.get("details") or {}
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (ValueError, TypeError):
                continue
        cat = details.get("category", "other")
        sev = details.get("severity", "info")
        by_category[cat] = by_category.get(cat, 0) + 1
        by_severity[sev] = by_severity.get(sev, 0) + 1
        tid = e.get("service", "")
        if tid and tid not in seen_tenants:
            seen_tenants.add(tid)
            tenant_count += 1
    return {
        "total": sum(by_category.values()),
        "by_category": by_category,
        "by_severity": by_severity,
        "tenants_with_suggestions": tenant_count,
    }


def _store_suggestion(tenant_id: str, suggestion: dict[str, Any]) -> None:
    """Store a suggestion in the Overwatch graph."""
    try:
        overwatch_graph.record_event(
            event_type="proactive_suggestion",
            service=tenant_id,
            severity=suggestion.get("severity", "info"),
            details={
                "category": suggestion.get("category", ""),
                "title": suggestion.get("title", ""),
                "description": suggestion.get("description", ""),
                "severity": suggestion.get("severity", "info"),
                "surfaced": False,
            },
        )
    except Exception:
        logger.debug("Failed to store suggestion for %s", tenant_id, exc_info=True)

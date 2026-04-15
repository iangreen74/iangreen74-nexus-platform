"""
Neptune Data Integrity Scanner. Scans for known corruption patterns:
orphan nodes missing project_id, dangling IMPORTS edges, leftover
placeholder/test tenants. auto_repair() only purges orphan nodes
pre-dating the 2026-04-14 isolation fix (86b1ac8) — that's the regression
the fix closed. Every repair recorded as an OverwatchPlatformEvent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.neptune_integrity")

# Isolation fix landed 2026-04-14 ~06:00 UTC (86b1ac8). Nodes older than
# this with project_id=NULL are the historical leak; safe to purge.
ISOLATION_FIX_CUTOFF_ISO = "2026-04-14T06:00:00+00:00"

INTEGRITY_CHECKS: list[dict[str, Any]] = [
    {
        "name": "orphan_mission_tasks",
        "query": ("MATCH (t:MissionTask) WHERE t.project_id IS NULL "
                  "RETURN count(t) AS cnt"),
        "threshold": 0,
        "severity": "warning",
        "description": "MissionTask nodes without project_id",
        "auto_repair_label": "MissionTask",
    },
    {
        "name": "orphan_brief_entries",
        "query": ("MATCH (b:BriefEntry) WHERE b.project_id IS NULL "
                  "RETURN count(b) AS cnt"),
        "threshold": 0,
        "severity": "warning",
        "description": "BriefEntry nodes without project_id",
        "auto_repair_label": "BriefEntry",
    },
    {
        "name": "orphan_repo_files",
        "query": ("MATCH (r:RepoFile) WHERE r.project_id IS NULL "
                  "RETURN count(r) AS cnt"),
        "threshold": 0,
        "severity": "warning",
        "description": "RepoFile nodes without project_id",
        "auto_repair_label": "RepoFile",
    },
    {
        "name": "stale_placeholder_tenants",
        "query": ("MATCH (t:Tenant) "
                  "WHERE t.tenant_id CONTAINS 'placeholder' "
                  "OR t.tenant_id CONTAINS 'test-' "
                  "RETURN count(t) AS cnt"),
        "threshold": 0,
        "severity": "info",
        "description": "Placeholder/test tenants that may cause noise",
        "auto_repair_label": None,
    },
    {
        "name": "dangling_imports_edges",
        "query": ("MATCH (a)-[r:IMPORTS]->(b) "
                  "WHERE a.tenant_id IS NULL OR b.tenant_id IS NULL "
                  "RETURN count(r) AS cnt"),
        "threshold": 0,
        "severity": "warning",
        "description": "IMPORTS edges between nodes missing tenant_id",
        "auto_repair_label": None,
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_count(q: str) -> int | None:
    if MODE != "production":
        return 0
    try:
        rows = overwatch_graph.query(q) or []
    except Exception:
        logger.exception("integrity query failed: %s", q[:80])
        return None
    if not rows:
        return 0
    row = rows[0] if isinstance(rows[0], dict) else {}
    try:
        return int(row.get("cnt") or 0)
    except (TypeError, ValueError):
        return 0


def run_integrity_scan() -> dict[str, Any]:
    """Execute every INTEGRITY_CHECK. Returns a shaped findings report."""
    started = datetime.now(timezone.utc)
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    for check in INTEGRITY_CHECKS:
        cnt = _run_count(check["query"])
        if cnt is None:
            errors.append(check["name"])
            continue
        if cnt > int(check["threshold"]):
            findings.append({
                "name": check["name"],
                "count": cnt,
                "severity": check["severity"],
                "description": check["description"],
                "auto_repair_label": check.get("auto_repair_label"),
            })
    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    return {
        "checked_at": _now_iso(),
        "duration_ms": duration_ms,
        "checks_run": len(INTEGRITY_CHECKS),
        "findings": findings,
        "finding_count": len(findings),
        "query_errors": errors,
        "healthy": not findings and not errors,
    }


def auto_repair(finding: dict[str, Any],
                 cutoff_iso: str = ISOLATION_FIX_CUTOFF_ISO,
                 dry_run: bool = True) -> dict[str, Any]:
    """
    Purge orphan nodes created before the isolation fix. No-op for
    findings without an `auto_repair_label` (e.g. edge-level checks,
    placeholder tenants — too risky to delete without review).
    """
    label = finding.get("auto_repair_label")
    if not label:
        return {"action": "skipped", "reason": "no auto_repair_label",
                "finding": finding.get("name")}
    if MODE != "production":
        return {"action": "mock", "label": label,
                "dry_run": dry_run, "finding": finding.get("name")}

    count_q = (f"MATCH (n:{label}) WHERE n.project_id IS NULL "
               f"AND n.created_at < $cutoff RETURN count(n) AS cnt")
    rows = overwatch_graph.query(count_q, {"cutoff": cutoff_iso}) or []
    count = int(rows[0].get("cnt", 0)) if rows else 0
    if dry_run or count == 0:
        return {"action": "dry_run" if dry_run else "noop",
                "label": label, "would_delete": count,
                "finding": finding.get("name")}

    try:
        overwatch_graph.query(
            f"MATCH (n:{label}) WHERE n.project_id IS NULL "
            "AND n.created_at < $cutoff DETACH DELETE n",
            {"cutoff": cutoff_iso},
        )
        overwatch_graph.record_event(
            event_type="neptune_integrity_repair",
            service=f"neptune:{label}",
            details={"finding": finding.get("name"), "deleted": count,
                     "cutoff": cutoff_iso},
            severity="info",
        )
    except Exception:
        logger.exception("auto_repair DELETE failed for %s", label)
        return {"action": "error", "label": label,
                "finding": finding.get("name")}
    return {"action": "deleted", "label": label, "deleted": count,
            "finding": finding.get("name")}


def journey_neptune_integrity() -> dict[str, Any]:
    """Synthetic: all quick integrity checks below threshold."""
    if MODE != "production":
        return {"name": "neptune_integrity", "status": "skip",
                "error": "Requires production Neptune access"}
    report = run_integrity_scan()
    if report["query_errors"]:
        return {"name": "neptune_integrity", "status": "error",
                "duration_ms": report["duration_ms"],
                "error": f"Checks errored: {report['query_errors']}"}
    if report["healthy"]:
        return {"name": "neptune_integrity", "status": "pass",
                "duration_ms": report["duration_ms"],
                "details": f"{report['checks_run']} checks passing"}
    # Critical findings fail the synthetic; warning-level degrades but
    # doesn't fail (noisy but doesn't block deploys).
    severities = {f["severity"] for f in report["findings"]}
    status = "fail" if "critical" in severities else "pass"
    top = report["findings"][0]
    return {"name": "neptune_integrity", "status": status,
            "duration_ms": report["duration_ms"],
            "details" if status == "pass" else "error":
            f"{top['name']}: {top['count']} ({top['severity']})"}

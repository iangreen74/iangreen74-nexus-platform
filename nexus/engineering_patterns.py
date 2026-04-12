"""
Engineering Pattern Learning — cross-tenant intelligence for optimization.

Unlike failure patterns (reactive), engineering patterns are PROACTIVE:
- "Deploys between 14:00-18:00 UTC have 95% success vs 71% off-hours"
- "Tenants with >5 PRs merged have 0 deploy failures"
- "Median PR cycle time is 47 minutes across all tenants"

Privacy rule: patterns are ANONYMOUS — aggregate statistics only.
Surfacing rule: >=3 data points required before a pattern is reported.

Sources:
1. Deploy outcomes (from deploy_patterns.py)
2. Tenant health snapshots (from overwatch graph)
3. CI results (from ci_reader.py)
4. PR/task velocity (from ground truth sensor)
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger(__name__)

MIN_DATA_POINTS = 3  # Never surface patterns from fewer observations


def analyze_deploy_timing() -> dict[str, Any] | None:
    """Analyze deploy success rate by time-of-day bucket."""
    events = overwatch_graph.get_recent_events(limit=500)
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "failed": 0})

    for e in events:
        if e.get("event_type") != "deploy_outcome":
            continue
        ts = e.get("created_at", "")
        details = _parse_details(e)
        if not ts or not details:
            continue
        try:
            hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
        except (ValueError, TypeError):
            continue
        bucket = "business" if 9 <= hour < 18 else "off_hours"
        buckets[bucket]["total"] += 1
        if details.get("status") in ("failed", "rollback"):
            buckets[bucket]["failed"] += 1

    if all(b["total"] < MIN_DATA_POINTS for b in buckets.values()):
        return None

    rates = {}
    for name, b in buckets.items():
        if b["total"] >= MIN_DATA_POINTS:
            rates[name] = round((b["total"] - b["failed"]) / b["total"] * 100, 1)

    return {
        "type": "deploy_timing",
        "rates": rates,
        "insight": _timing_insight(rates),
        "data_points": sum(b["total"] for b in buckets.values()),
    }


def analyze_pr_velocity() -> dict[str, Any] | None:
    """Compute cross-tenant PR velocity statistics."""
    if MODE != "production":
        return {
            "type": "pr_velocity",
            "median_cycle_minutes": 47,
            "tenants_measured": 3,
            "insight": "Median PR cycle: 47 minutes across 3 tenants",
        }
    try:
        from nexus.sensors.ground_truth import get_velocity
        from nexus.neptune_client import get_tenant_ids

        cycles = []
        for tid in get_tenant_ids():
            v = get_velocity(tid)
            if v.get("avg_pr_cycle_minutes"):
                cycles.append(v["avg_pr_cycle_minutes"])
        if len(cycles) < MIN_DATA_POINTS:
            return None
        cycles.sort()
        median = cycles[len(cycles) // 2]
        return {
            "type": "pr_velocity",
            "median_cycle_minutes": round(median, 1),
            "tenants_measured": len(cycles),
            "insight": f"Median PR cycle: {median:.0f} minutes across {len(cycles)} tenants",
        }
    except Exception:
        return None


def analyze_failure_categories() -> dict[str, Any] | None:
    """Categorize recent failures by root cause."""
    events = overwatch_graph.get_recent_events(limit=300)
    categories: dict[str, int] = defaultdict(int)

    for e in events:
        if e.get("event_type") not in ("deploy_outcome", "support_escalation"):
            continue
        details = _parse_details(e)
        status = details.get("status", "")
        if status in ("failed", "rollback"):
            categories["deploy_failure"] += 1
        issue = details.get("issue", "")
        if "permission" in issue.lower():
            categories["permission"] += 1
        if "timeout" in issue.lower():
            categories["timeout"] += 1

    significant = {k: v for k, v in categories.items() if v >= MIN_DATA_POINTS}
    if not significant:
        return None
    return {
        "type": "failure_categories",
        "categories": dict(significant),
        "insight": f"Top failure: {max(significant, key=significant.get)} ({max(significant.values())}x)",
    }


def get_recommendations(limit: int = 3) -> list[dict[str, Any]]:
    """Get the top actionable engineering recommendations."""
    patterns = analyze_all()
    recs: list[dict[str, Any]] = []
    for p in patterns:
        if p and p.get("insight"):
            recs.append({
                "type": p["type"],
                "insight": p["insight"],
                "data_points": p.get("data_points", p.get("tenants_measured", 0)),
            })
    return recs[:limit]


def analyze_all() -> list[dict[str, Any] | None]:
    """Run all engineering pattern analyses."""
    return [
        analyze_deploy_timing(),
        analyze_pr_velocity(),
        analyze_failure_categories(),
    ]


# --- Helpers ------------------------------------------------------------------


def _parse_details(event: dict[str, Any]) -> dict[str, Any]:
    details = event.get("details") or {}
    if isinstance(details, str):
        try:
            return json.loads(details)
        except (ValueError, TypeError):
            return {}
    return details


def _timing_insight(rates: dict[str, float]) -> str:
    biz = rates.get("business")
    off = rates.get("off_hours")
    if biz is not None and off is not None:
        return f"Deploy success: {biz}% during business hours vs {off}% off-hours"
    if biz is not None:
        return f"Deploy success: {biz}% during business hours"
    if off is not None:
        return f"Deploy success: {off}% off-hours"
    return "Insufficient data for timing analysis"

"""
Performance Sensor — rolling metrics and anomaly detection.

Tracks measurable dimensions of platform + tenant performance,
computes baselines from the Overwatch graph, and flags anomalies
(>2σ from 7-day mean) so triage can investigate before they become failures.

Functions return dicts with `value`, `stats` (mean, stddev, p50, p95),
`trend` (improving/stable/degrading), and `anomalous` (bool).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import neptune_client
from nexus.config import MODE

logger = logging.getLogger("nexus.sensors.performance")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stats(values: list[float]) -> dict[str, float | None]:
    """Compute basic stats for a list of numeric values."""
    if not values:
        return {"mean": None, "stddev": None, "p50": None, "p95": None, "count": 0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
    stddev = math.sqrt(variance)
    sv = sorted(values)
    p50 = sv[n // 2] if n else None
    p95 = sv[int(n * 0.95)] if n > 1 else sv[-1] if sv else None
    return {"mean": round(mean, 2), "stddev": round(stddev, 2), "p50": round(p50, 2), "p95": round(p95, 2), "count": n}


def _trend(values: list[float]) -> str:
    """Simple trend: compare last third to first third."""
    if len(values) < 6:
        return "unknown"
    third = len(values) // 3
    first_avg = sum(values[:third]) / third
    last_avg = sum(values[-third:]) / third
    if first_avg == 0:
        return "stable"
    pct_change = (last_avg - first_avg) / abs(first_avg)
    if pct_change > 0.1:
        return "degrading"
    if pct_change < -0.1:
        return "improving"
    return "stable"


def _is_anomalous(value: float, mean: float | None, stddev: float | None, threshold: float = 2.0) -> bool:
    """True if value is >threshold standard deviations from mean."""
    if mean is None or stddev is None or stddev == 0:
        return False
    return abs(value - mean) > threshold * stddev


def daemon_cycle_performance(hours: int = 24) -> dict[str, Any]:
    """
    Daemon cycle duration performance over the last `hours`.

    Returns: latest, stats (mean/stddev/p50/p95), trend, anomalous.
    """
    rows = neptune_client.query(
        "MATCH (d:DaemonCycle) WHERE d.timestamp >= $cutoff "
        "RETURN d.duration_seconds AS dur, d.timestamp AS ts "
        "ORDER BY d.timestamp",
        {"cutoff": (_now() - timedelta(hours=hours)).isoformat()},
    )
    if MODE != "production" and not rows:
        # Local mock: simulate a healthy daemon
        rows = [{"dur": 30 + i * 0.5, "ts": (_now() - timedelta(minutes=i * 2)).isoformat()} for i in range(20)]

    durations = []
    for r in rows:
        try:
            durations.append(float(r.get("dur", 0)))
        except (TypeError, ValueError):
            continue

    stats = _stats(durations)
    latest = durations[-1] if durations else None
    trend = _trend(durations)
    anomalous = _is_anomalous(latest, stats.get("mean"), stats.get("stddev")) if latest is not None else False

    return {
        "latest": latest,
        "stats": stats,
        "trend": trend,
        "anomalous": anomalous,
        "sample_count": len(durations),
        "hours": hours,
    }


def pr_generation_time(tenant_id: str, hours: int = 168) -> dict[str, Any]:
    """
    PR generation time: task created → PR submitted, per tenant.
    """
    rows = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid}) "
        "WHERE m.submitted_at IS NOT NULL AND m.created_at IS NOT NULL "
        "RETURN m.created_at AS created, m.submitted_at AS submitted "
        "ORDER BY m.created_at",
        {"tid": tenant_id},
    )
    if MODE != "production" and not rows:
        return {"mean_hours": 2.5, "stats": _stats([2.0, 2.5, 3.0, 2.2, 2.8]), "trend": "stable", "sample_count": 5}

    deltas = []
    for r in rows:
        try:
            c = datetime.fromisoformat(str(r["created"]).replace("Z", "+00:00"))
            s = datetime.fromisoformat(str(r["submitted"]).replace("Z", "+00:00"))
            deltas.append((s - c).total_seconds() / 3600.0)
        except Exception:
            continue

    stats = _stats(deltas)
    return {
        "mean_hours": stats.get("mean"),
        "stats": stats,
        "trend": _trend(deltas),
        "sample_count": len(deltas),
    }


def task_velocity(tenant_id: str, hours: int = 168) -> dict[str, Any]:
    """
    Tasks completed per day for a tenant, over the last `hours`.
    """
    cutoff = (_now() - timedelta(hours=hours)).isoformat()
    rows = neptune_client.query(
        "MATCH (m:MissionTask {tenant_id: $tid}) "
        "WHERE m.created_at >= $cutoff "
        "RETURN m.status AS status, m.created_at AS created",
        {"tid": tenant_id, "cutoff": cutoff},
    )
    if MODE != "production" and not rows:
        return {"tasks_per_day": 2.0, "daily_counts": [2, 3, 1, 2, 2, 3, 1], "trend": "stable", "total": 14}

    # Bucket by day
    days: dict[str, int] = {}
    for r in rows:
        try:
            ts = str(r.get("created", ""))[:10]
            if ts:
                days[ts] = days.get(ts, 0) + 1
        except Exception:
            continue

    daily_counts = list(days.values()) if days else []
    total = sum(daily_counts)
    num_days = max(len(daily_counts), 1)
    return {
        "tasks_per_day": round(total / num_days, 1),
        "daily_counts": daily_counts,
        "trend": _trend([float(c) for c in daily_counts]) if len(daily_counts) >= 3 else "unknown",
        "total": total,
    }


def context_health(tenant_id: str) -> dict[str, Any]:
    """
    Accretion Core context health: how many intelligence sources are active.

    Expected sources (from aria's Omniscience stack):
    temporal, intent, predictive, emotional, omniscience + base accretion.
    We check for recent nodes from each source type.
    """
    expected_sources = [
        ("TrajectoryInsight", "temporal"),
        ("IntentSnapshot", "intent"),
        ("PredictedTask", "predictive"),
        ("EmotionalState", "emotional"),
        ("OmniscientInsight", "omniscience"),
        ("AnalysisReport", "analysis"),
        ("ConventionRule", "conventions"),
        ("SecurityFinding", "security"),
    ]
    if MODE != "production":
        return {
            "active": 6,
            "expected": len(expected_sources),
            "missing": ["predictive", "omniscience"],
            "healthy": True,
        }

    cutoff = (_now() - timedelta(days=7)).isoformat()
    active = 0
    missing: list[str] = []
    for label, name in expected_sources:
        rows = neptune_client.query(
            f"MATCH (n:{label} {{tenant_id: $tid}}) WHERE n.generated_at >= $cutoff OR n.created_at >= $cutoff "
            "RETURN count(n) AS c LIMIT 1",
            {"tid": tenant_id, "cutoff": cutoff},
        )
        count = int(rows[0].get("c", 0)) if rows else 0
        if count > 0:
            active += 1
        else:
            missing.append(name)

    return {
        "active": active,
        "expected": len(expected_sources),
        "missing": missing,
        "healthy": active >= 4,
    }

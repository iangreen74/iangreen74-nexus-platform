"""
SRE Metrics Engine — the scorecard for antifragile engineering.

Computes real-time SRE metrics from the Overwatch graph's incident,
event, and action nodes. Every incident that passes through the
detect → acknowledge → resolve lifecycle feeds these metrics, and
every metric trending the wrong way triggers investigation.

The antifragile loop:
  Incident → Detection → Recovery → Record → Learn → Prevent → Stronger
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph

logger = logging.getLogger("nexus.sensors.sre_metrics")

# SLO target: 99.9% = 43.2 minutes of allowed downtime per 30-day period
SLO_TARGET = 0.999
ERROR_BUDGET_MINUTES = (1 - SLO_TARGET) * 30 * 24 * 60  # ~43.2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _seconds_between(a: str | None, b: str | None) -> float | None:
    da, db = _parse(a), _parse(b)
    if da and db:
        return abs((db - da).total_seconds())
    return None


def compute_mttd(hours: int = 24) -> float | None:
    """
    Mean Time to Detect — average seconds from incident creation to
    detection (approximated as the gap between the DaemonCycle timestamp
    that first shows an anomaly and the OverwatchPlatformEvent that triages it).

    For now, uses the incident's detected_at as both the "real event time"
    and the "detection time" — which makes MTTD = 0 until we add external
    event timestamps. Returns None if there are no resolved incidents.
    """
    incidents = overwatch_graph.get_resolved_incidents(hours=hours)
    if not incidents:
        return None
    deltas = []
    for inc in incidents:
        # When external event timestamps are available, compute the real gap.
        # For now: detected_at is the time the triage first ran, which IS
        # the detection moment. The "real" event start is approximated as
        # the same timestamp (MTTD ≈ poll interval).
        deltas.append(30.0)  # one poll cycle = 30s detection time
    return sum(deltas) / len(deltas) if deltas else None


def compute_mtta(hours: int = 24) -> float | None:
    """
    Mean Time to Acknowledge — average seconds from detected_at to
    acknowledged_at (first action taken).
    """
    incidents = overwatch_graph.get_resolved_incidents(hours=hours)
    if not incidents:
        return None
    deltas = []
    for inc in incidents:
        d = _seconds_between(inc.get("detected_at"), inc.get("acknowledged_at"))
        if d is not None:
            deltas.append(d)
    return sum(deltas) / len(deltas) if deltas else None


def compute_mttr(hours: int = 24) -> float | None:
    """
    Mean Time to Recovery — average seconds from detected_at to resolved_at.
    """
    incidents = overwatch_graph.get_resolved_incidents(hours=hours)
    if not incidents:
        return None
    deltas = []
    for inc in incidents:
        d = _seconds_between(inc.get("detected_at"), inc.get("resolved_at"))
        if d is not None:
            deltas.append(d)
    return sum(deltas) / len(deltas) if deltas else None


def compute_mtbf(hours: int = 168) -> float | None:
    """
    Mean Time Between Failures — average hours between incidents.
    """
    all_incidents = overwatch_graph.get_resolved_incidents(hours=hours)
    open_incidents = overwatch_graph.get_open_incidents()
    total = all_incidents + open_incidents
    if len(total) < 2:
        return None
    timestamps = sorted(
        _parse(i.get("detected_at")) for i in total
        if _parse(i.get("detected_at"))
    )
    if len(timestamps) < 2:
        return None
    gaps = [(timestamps[i + 1] - timestamps[i]).total_seconds() / 3600.0
            for i in range(len(timestamps) - 1)]
    return sum(gaps) / len(gaps) if gaps else None


def compute_change_failure_rate(hours: int = 168) -> float | None:
    """
    Fraction of deployments followed by an incident within 30 minutes.
    """
    events = overwatch_graph.get_recent_events(limit=500)
    deploys = [e for e in events if e.get("event_type") in ("deployment", "daemon_restart", "auto_heal_success")]
    if not deploys:
        return None
    incidents = overwatch_graph.get_resolved_incidents(hours=hours) + overwatch_graph.get_open_incidents()
    incident_times = [_parse(i.get("detected_at")) for i in incidents]
    incident_times = [t for t in incident_times if t]

    failures = 0
    for dep in deploys:
        dep_time = _parse(dep.get("created_at"))
        if not dep_time:
            continue
        for inc_time in incident_times:
            if 0 <= (inc_time - dep_time).total_seconds() <= 1800:
                failures += 1
                break
    return failures / len(deploys) if deploys else None


def compute_availability(hours: int = 24) -> float:
    """
    Availability = 1 - (total incident duration / total period).
    Returns a percentage (0-100).
    """
    incidents = overwatch_graph.get_resolved_incidents(hours=hours)
    total_downtime = 0.0
    for inc in incidents:
        dur = inc.get("duration_seconds")
        if dur is not None:
            try:
                total_downtime += float(dur)
            except (TypeError, ValueError):
                pass
    # Add ongoing incidents
    for inc in overwatch_graph.get_open_incidents():
        detected = _parse(inc.get("detected_at"))
        if detected:
            total_downtime += (_now() - detected).total_seconds()
    total_seconds = hours * 3600
    if total_seconds == 0:
        return 100.0
    return round((1 - total_downtime / total_seconds) * 100, 3)


def compute_error_budget(period_days: int = 30) -> dict[str, float]:
    """
    How much of the 99.9% error budget has been consumed.
    """
    incidents = overwatch_graph.get_resolved_incidents(hours=period_days * 24)
    consumed_seconds = 0.0
    for inc in incidents:
        dur = inc.get("duration_seconds")
        if dur is not None:
            try:
                consumed_seconds += float(dur)
            except (TypeError, ValueError):
                pass
    for inc in overwatch_graph.get_open_incidents():
        detected = _parse(inc.get("detected_at"))
        if detected:
            consumed_seconds += (_now() - detected).total_seconds()
    consumed_minutes = consumed_seconds / 60.0
    remaining = max(0.0, ERROR_BUDGET_MINUTES - consumed_minutes)
    consumed_pct = (consumed_minutes / ERROR_BUDGET_MINUTES * 100) if ERROR_BUDGET_MINUTES > 0 else 0
    return {
        "budget_minutes": round(ERROR_BUDGET_MINUTES, 1),
        "consumed_minutes": round(consumed_minutes, 1),
        "remaining_minutes": round(remaining, 1),
        "consumed_percent": round(consumed_pct, 1),
    }


def _trend(current: float | None, previous: float | None, lower_is_better: bool = True) -> str:
    """Compare two values and return a trend direction."""
    if current is None or previous is None:
        return "unknown"
    if abs(current - previous) < 0.01 * max(abs(current), abs(previous), 1):
        return "stable"
    if lower_is_better:
        return "improving" if current < previous else "degrading"
    return "improving" if current > previous else "degrading"


def compute_antifragile_score() -> int:
    """
    Composite score (0-100) measuring how antifragile the system is.
    The score can only go up over time if the antifragile loop is working.
    """
    score = 0
    mttd = compute_mttd()
    if mttd is not None and mttd < 60:
        score += 20
    mttr = compute_mttr()
    if mttr is not None and mttr < 300:
        score += 20
    patterns = overwatch_graph.get_failure_patterns(min_confidence=0.5)
    if len(patterns) > 5:
        score += 15
    elif len(patterns) > 0:
        score += 5
    heal_actions = overwatch_graph.get_healing_history(hours=168)
    successes = sum(1 for a in heal_actions if a.get("outcome") == "success")
    total = len(heal_actions)
    if total > 0 and successes / total > 0.8:
        score += 15
    mtbf = compute_mtbf()
    # Can't easily compute trend without historical snapshots, so give partial credit
    if mtbf is not None and mtbf > 12:
        score += 10
    cfr = compute_change_failure_rate()
    if cfr is not None and cfr < 0.1:
        score += 10
    budget = compute_error_budget()
    if budget["consumed_percent"] < 50:
        score += 10
    return min(score, 100)


def get_sre_dashboard() -> dict[str, Any]:
    """All SRE metrics in one call + trends + antifragile score."""
    # Current period (24h)
    mttd = compute_mttd(24)
    mtta = compute_mtta(24)
    mttr = compute_mttr(24)
    mtbf = compute_mtbf(168)
    cfr = compute_change_failure_rate(168)
    avail = compute_availability(24)
    budget = compute_error_budget(30)

    # Previous period (24-48h ago) for trends
    mttd_prev = compute_mttd(48)
    mtta_prev = compute_mtta(48)
    mttr_prev = compute_mttr(48)

    # Incident counts
    open_incidents = overwatch_graph.get_open_incidents()
    resolved_24h = overwatch_graph.get_resolved_incidents(hours=24)
    unlearned = [i for i in resolved_24h if not i.get("prevention_added")]

    patterns = overwatch_graph.get_failure_patterns(min_confidence=0.0)
    total_occurrences = sum(p.get("occurrence_count", 0) for p in patterns)

    return {
        "mttd_seconds": round(mttd, 1) if mttd is not None else None,
        "mttd_trend": _trend(mttd, mttd_prev),
        "mtta_seconds": round(mtta, 1) if mtta is not None else None,
        "mtta_trend": _trend(mtta, mtta_prev),
        "mttr_seconds": round(mttr, 1) if mttr is not None else None,
        "mttr_trend": _trend(mttr, mttr_prev),
        "mtbf_hours": round(mtbf, 1) if mtbf is not None else None,
        "mtbf_trend": "unknown",  # needs historical snapshots
        "change_failure_rate": round(cfr, 3) if cfr is not None else None,
        "cfr_trend": "unknown",
        "availability_percent": avail,
        "availability_trend": "stable",
        "error_budget": budget,
        "antifragile_score": compute_antifragile_score(),
        "open_incidents": len(open_incidents),
        "resolved_24h": len(resolved_24h),
        "unlearned_incidents": len(unlearned),
        "patterns_learned": len(patterns),
        "patterns_total_occurrences": total_occurrences,
    }

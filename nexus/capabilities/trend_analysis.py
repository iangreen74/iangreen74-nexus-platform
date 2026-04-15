"""
Trend Analysis — turn point-in-time metrics into trajectories.

Overwatch reports used to say "CI green rate 86%". That tells the reader
a number but not whether it's getting better or worse, or how far from
the target. This module stores metric snapshots and replays them as
linear projections so reports can say:

  "CI green rate 86% (improving from 83%, projected to reach 95% by
   ~06:00 UTC at the current +1.5%/hr rate)."

Storage uses the existing OverwatchPlatformEvent label with a dedicated
event_type so we don't need a new Neptune label — simpler, and events
already age out cleanly with the 24h window.

Linear projection is deliberate: simple, interpretable, and no ML
runtime dependency. Targets are per-metric ceilings (CI green rate 95%,
synthetic pass rate 100%) or floors (daemon errors 0).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import overwatch_graph
from nexus.config import MODE

logger = logging.getLogger("nexus.capabilities.trend_analysis")

_EVENT_TYPE = "metric_snapshot"

# Per-metric targets and whether we're moving UP toward them (green rate)
# or DOWN (daemon error rate). Improving direction is relative to the
# target, not the raw arithmetic delta.
TARGETS: dict[str, dict[str, Any]] = {
    "ci_green_rate":         {"target": 0.95, "direction": "up"},
    "ci_recent_green_rate":  {"target": 0.95, "direction": "up"},
    "synthetic_pass_rate":   {"target": 1.0,  "direction": "up"},
    "daemon_error_rate":     {"target": 0.0,  "direction": "down"},
    "open_incidents":        {"target": 0.0,  "direction": "down"},
}

_STABLE_EPSILON = 1e-4  # per-hour delta below this counts as "stable"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def record_metric(name: str, value: float,
                   tags: dict[str, Any] | None = None) -> str:
    """Persist a single (name, value) snapshot for later trend analysis."""
    details: dict[str, Any] = {"metric": name, "value": float(value)}
    if tags:
        details["tags"] = tags
    try:
        return overwatch_graph.record_event(
            event_type=_EVENT_TYPE,
            service=name,
            details=details,
            severity="info",
        )
    except Exception:
        logger.exception("record_metric(%s) failed", name)
        return ""


def _history_from_local(name: str, cutoff_iso: str) -> list[dict[str, Any]]:
    with overwatch_graph._lock:
        rows = [
            dict(n) for n in overwatch_graph._local_store.get(
                "OverwatchPlatformEvent", []) or []
            if n.get("event_type") == _EVENT_TYPE
            and n.get("service") == name
            and n.get("created_at", "") >= cutoff_iso
        ]
    return sorted(rows, key=lambda r: r.get("created_at", ""))


def get_metric_history(name: str,
                        lookback_hours: int = 24) -> list[tuple[datetime, float]]:
    """Return (timestamp, value) pairs newest-last within the lookback."""
    cutoff_dt = _now() - timedelta(hours=lookback_hours)
    cutoff_iso = cutoff_dt.isoformat()
    if MODE != "production":
        rows = _history_from_local(name, cutoff_iso)
    else:
        try:
            rows = overwatch_graph.query(
                "MATCH (e:OverwatchPlatformEvent) "
                "WHERE e.event_type = $et AND e.service = $svc "
                "AND e.created_at >= $cutoff "
                "RETURN e.details AS details, e.created_at AS created_at "
                "ORDER BY e.created_at",
                {"et": _EVENT_TYPE, "svc": name, "cutoff": cutoff_iso},
            ) or []
        except Exception:
            logger.exception("get_metric_history(%s) query failed", name)
            return []

    out: list[tuple[datetime, float]] = []
    for r in rows:
        ts = _parse_iso(r.get("created_at"))
        if ts is None:
            continue
        raw = r.get("details")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                continue
        if not isinstance(raw, dict) or "value" not in raw:
            continue
        try:
            out.append((ts, float(raw["value"])))
        except (TypeError, ValueError):
            continue
    return out


def compute_trend(metric_name: str, current_value: float,
                   lookback_hours: int = 24,
                   target: float | None = None) -> dict[str, Any]:
    """
    Classify a metric's trajectory against its target. Returns:
      current, previous, direction (improving|degrading|stable),
      rate (change per hour), projected_threshold_time, target, samples.
    """
    spec = TARGETS.get(metric_name, {})
    tgt = target if target is not None else spec.get("target")
    polarity = spec.get("direction", "up")  # up = higher is better
    history = get_metric_history(metric_name, lookback_hours)

    previous = history[0][1] if history else current_value
    now = _now()
    duration_h = ((now - history[0][0]).total_seconds() / 3600.0
                  if history else 0.0)
    rate_per_hour = ((current_value - previous) / duration_h
                     if duration_h > 0 else 0.0)

    # Map raw delta to improvement semantics given target direction.
    if abs(rate_per_hour) < _STABLE_EPSILON or duration_h == 0:
        direction = "stable"
    elif polarity == "up":
        direction = "improving" if rate_per_hour > 0 else "degrading"
    else:
        direction = "improving" if rate_per_hour < 0 else "degrading"

    projected: str | None = None
    if tgt is not None and direction == "improving":
        remaining = tgt - current_value if polarity == "up" else current_value - tgt
        rate_mag = abs(rate_per_hour)
        if remaining > 0 and rate_mag > 0:
            hours_to_target = remaining / rate_mag
            if hours_to_target < 24 * 14:  # don't bother projecting past 2 weeks
                projected = (now + timedelta(hours=hours_to_target)).isoformat()

    return {
        "metric": metric_name,
        "current": current_value,
        "previous": previous,
        "direction": direction,
        "rate": round(rate_per_hour, 6),
        "projected_threshold_time": projected,
        "target": tgt,
        "samples": len(history),
        "lookback_hours": lookback_hours,
    }


def summarize(trend: dict[str, Any]) -> str:
    """One-line English summary safe for injection into a synthesis prompt."""
    name = trend.get("metric", "metric")
    cur = trend.get("current")
    direction = trend.get("direction", "stable")
    rate = trend.get("rate", 0) or 0
    tgt = trend.get("target")
    parts = [f"{name} {cur}"]
    if direction == "stable":
        parts.append("(stable)")
    else:
        sign = "+" if rate > 0 else ""
        parts.append(f"({direction}, {sign}{rate:.4f}/hr)")
    proj = trend.get("projected_threshold_time")
    if proj and tgt is not None:
        parts.append(f"projected to reach target {tgt} by ~{proj[11:16]} UTC")
    return " ".join(parts)

"""
Daemon Monitor Sensor.

Watches the aria-daemon ECS service and verifies that its reasoning
loop is still turning. A running task with no recent cycle is just
as bad as a dead task — both produce a `stale: True` report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from nexus import aws_client, neptune_client
from nexus.config import (
    DAEMON_CYCLE_STALE_MINUTES,
    FORGEWING_CLUSTER,
    MODE,
)

logger = logging.getLogger("nexus.sensors.daemon")

DAEMON_SERVICE = "aria-daemon"
DAEMON_LOG_GROUP = "/ecs/aria-daemon"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _latest_cycle_from_graph() -> tuple[datetime | None, dict[str, Any]]:
    """
    Read the most recent DaemonCycle node from Neptune.

    The aria daemon writes a DaemonCycle node every iteration with a
    `timestamp` property (see aria/daemon_helpers.py:write_cycle_to_neptune).
    Returns (timestamp, raw_node_dict) — the dict carries the cycle counters
    so the report can surface throughput, not just freshness.
    """
    cycle = neptune_client.get_last_daemon_cycle()
    if not cycle:
        return None, {}
    ts_raw = cycle.get("timestamp")
    if not ts_raw:
        return None, cycle
    try:
        return datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")), cycle
    except ValueError:
        logger.warning("Unparseable DaemonCycle timestamp: %r", ts_raw)
        return None, cycle


def check_daemon() -> dict[str, Any]:
    """Build a DaemonHealthReport. Never raises."""
    try:
        svc = aws_client.get_ecs_service_status(FORGEWING_CLUSTER, DAEMON_SERVICE)
        running = svc.get("running_count", 0) > 0

        last_cycle, cycle = _latest_cycle_from_graph()
        stale = True
        age_minutes: float | None = None
        if last_cycle:
            age = _now() - last_cycle
            age_minutes = age.total_seconds() / 60.0
            stale = age_minutes > DAEMON_CYCLE_STALE_MINUTES

        errors = aws_client.get_cloudwatch_errors(DAEMON_LOG_GROUP, minutes=30)
        # Rough error rate: errors per minute over the window.
        error_rate = errors / 30.0

        return {
            "service": DAEMON_SERVICE,
            "running": running,
            "running_count": svc.get("running_count", 0),
            "desired_count": svc.get("desired_count", 0),
            "last_cycle_at": last_cycle.isoformat() if last_cycle else None,
            "cycle_age_minutes": age_minutes,
            "last_cycle_duration_seconds": cycle.get("duration_seconds"),
            "last_cycle_prs_checked": cycle.get("prs_checked"),
            "last_cycle_prs_merged": cycle.get("prs_merged"),
            "last_cycle_tasks_dispatched": cycle.get("tasks_dispatched"),
            "stale": stale if running else True,
            "error_count_30m": errors,
            "error_rate": round(error_rate, 3),
            "healthy": running and not stale and errors == 0,
            "checked_at": _now().isoformat(),
        }
    except Exception:
        logger.exception("check_daemon crashed")
        return {
            "service": DAEMON_SERVICE,
            "running": False,
            "stale": True,
            "healthy": False,
            "error": True,
            "checked_at": _now().isoformat(),
        }

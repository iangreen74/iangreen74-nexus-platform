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


def _latest_cycle_from_graph() -> datetime | None:
    """Derive last-cycle timestamp from the freshest task in Neptune."""
    if MODE != "production":
        return _now() - timedelta(minutes=3)
    try:
        tenants = neptune_client.get_tenant_ids()
        newest: datetime | None = None
        for tid in tenants:
            tasks = neptune_client.get_recent_tasks(tid, limit=1)
            if not tasks:
                continue
            ts_raw = tasks[0].get("created_at")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if newest is None or ts > newest:
                newest = ts
        return newest
    except Exception:
        logger.exception("_latest_cycle_from_graph failed")
        return None


def check_daemon() -> dict[str, Any]:
    """Build a DaemonHealthReport. Never raises."""
    try:
        svc = aws_client.get_ecs_service_status(FORGEWING_CLUSTER, DAEMON_SERVICE)
        running = svc.get("running_count", 0) > 0

        last_cycle = _latest_cycle_from_graph()
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

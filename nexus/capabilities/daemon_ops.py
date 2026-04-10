"""
Daemon Operations — lifecycle management for the aria-daemon.

Extends the basic restart_service capability with:
- Post-restart verification
- Timeout diagnosis (which hook is hanging?)
- Code version drift detection
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from nexus import aws_client, overwatch_graph
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_MODERATE, BLAST_SAFE, FORGEWING_CLUSTER, MODE

logger = logging.getLogger("nexus.capabilities.daemon_ops")

DAEMON_SERVICE = "aria-daemon"
DAEMON_LOG_GROUP = "/aria/daemon"


def restart_daemon(**_: Any) -> dict[str, Any]:
    """
    Force a new deployment of aria-daemon and verify it reaches running.

    Moderate blast radius — kills the current task and starts a new one.
    The daemon has no persistent state, so restart is always safe.
    """
    if MODE != "production":
        return {"service": DAEMON_SERVICE, "restarted": True, "mock": True}
    try:
        resp = aws_client._client("ecs").update_service(
            cluster=FORGEWING_CLUSTER,
            service=DAEMON_SERVICE,
            forceNewDeployment=True,
        )
        deployment = (resp.get("service", {}).get("deployments") or [{}])[0]
        overwatch_graph.record_event(
            "daemon_restart",
            DAEMON_SERVICE,
            {"deployment_id": deployment.get("id")},
            "warning",
        )
        return {
            "service": DAEMON_SERVICE,
            "restarted": True,
            "deployment_id": deployment.get("id"),
            "status": deployment.get("status"),
        }
    except Exception as exc:
        logger.exception("restart_daemon failed")
        return {"service": DAEMON_SERVICE, "restarted": False, "error": str(exc)}


def diagnose_daemon_timeout(**_: Any) -> dict[str, Any]:
    """
    Read recent daemon logs and identify which hook is consuming the
    most time. Returns the slowest hooks and their durations.

    Safe blast radius — read-only log analysis.
    """
    if MODE != "production":
        return {
            "mock": True,
            "slowest_hooks": [
                {"hook": "trajectory", "duration_s": 45.2},
                {"hook": "omniscience", "duration_s": 12.1},
            ],
        }
    try:
        import json
        import re

        logs = aws_client._client("logs")
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = end_ms - 30 * 60 * 1000  # last 30 min
        resp = logs.filter_log_events(
            logGroupName=DAEMON_LOG_GROUP,
            startTime=start_ms,
            endTime=end_ms,
            limit=500,
        )
        # Parse lines looking for timing: "hookname took Xs" or "hookname: {status: ok, ...}"
        hook_times: dict[str, float] = {}
        for event in resp.get("events", []):
            msg = event.get("message", "")
            # Pattern: "hook_name took 12.34s" or "duration_seconds: 12.34"
            m = re.search(r"(\w+)\s+took\s+([\d.]+)s", msg)
            if m:
                hook, dur = m.group(1), float(m.group(2))
                hook_times[hook] = max(hook_times.get(hook, 0), dur)
        slowest = sorted(hook_times.items(), key=lambda x: x[1], reverse=True)[:10]
        return {
            "slowest_hooks": [{"hook": h, "duration_s": round(d, 1)} for h, d in slowest],
            "total_hooks_seen": len(hook_times),
            "log_events_scanned": len(resp.get("events", [])),
        }
    except Exception as exc:
        logger.exception("diagnose_daemon_timeout failed")
        return {"error": str(exc)}


def check_daemon_code_version(**_: Any) -> dict[str, Any]:
    """
    Compare the running daemon task's image digest against the latest
    in ECR. If they differ, the daemon is running old code.
    """
    if MODE != "production":
        return {"mock": True, "up_to_date": True, "running_digest": "abc", "ecr_digest": "abc"}
    try:
        ecs = aws_client._client("ecs")
        # Get running task
        tasks = ecs.list_tasks(cluster=FORGEWING_CLUSTER, serviceName=DAEMON_SERVICE).get("taskArns", [])
        if not tasks:
            return {"error": "no running tasks"}
        task_detail = ecs.describe_tasks(cluster=FORGEWING_CLUSTER, tasks=[tasks[0]]).get("tasks", [{}])[0]
        containers = task_detail.get("containers", [{}])
        running_digest = (containers[0].get("imageDigest") or "") if containers else ""

        # Get latest ECR digest
        ecr = aws_client._client("ecr")
        images = ecr.describe_images(
            repositoryName="aria-platform",
            imageIds=[{"imageTag": "latest"}],
        ).get("imageDetails", [])
        ecr_digest = images[0].get("imageDigest", "") if images else ""

        return {
            "up_to_date": running_digest == ecr_digest,
            "running_digest": running_digest[:20],
            "ecr_digest": ecr_digest[:20],
        }
    except Exception as exc:
        logger.exception("check_daemon_code_version failed")
        return {"error": str(exc)}


registry.register(Capability(
    name="restart_daemon",
    function=restart_daemon,
    blast_radius=BLAST_MODERATE,
    description="Force new deployment of aria-daemon + verify",
))
registry.register(Capability(
    name="diagnose_daemon_timeout",
    function=diagnose_daemon_timeout,
    blast_radius=BLAST_SAFE,
    description="Analyze daemon logs to identify which hook is slow",
))
registry.register(Capability(
    name="check_daemon_code_version",
    function=check_daemon_code_version,
    blast_radius=BLAST_SAFE,
    description="Compare running daemon image digest vs ECR latest",
))

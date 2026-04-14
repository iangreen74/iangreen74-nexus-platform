"""
CI Healer — surgical remediation for hung GitHub Actions runners.

Given a CIIncident from ci_heartbeat, locate the self-hosted runner,
find the wedged Runner.Worker via SSM, and kill it. Exact fix from the
2026-04-14 outage, automated.

Safety rails:
- Only `Runner.Worker` processes — NEVER Runner.Listener (losing the
  listener disconnects the runner from GitHub).
- Circuit breaker: ≤3 kills/hour/runner. Beyond that, human review.
- No systemd restarts (human decision — loses in-flight work).
- Every kill recorded as HealingAction for audit.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

from nexus import overwatch_graph
from nexus.aws_client import _client
from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_MODERATE, MODE

logger = logging.getLogger("nexus.capabilities.ci_healer")

MAX_KILLS_PER_HOUR_PER_RUNNER = 3
IDLE_CPU_THRESHOLD_PCT = 1.0  # <1% CPU = wedged
MIN_HUNG_ELAPSED_SEC = 300     # worker must be at least 5min old to kill

# Per-runner kill-time deques for the circuit breaker. Keyed by runner
# name (or instance id when name isn't known). In-memory is fine —
# overflow just means we refuse a kill and escalate, which is the safe
# failure mode.
_kill_history: dict[str, deque[datetime]] = {}
_history_lock = Lock()


def _record_kill(runner: str) -> None:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    with _history_lock:
        dq = _kill_history.setdefault(runner, deque())
        while dq and dq[0] < cutoff:
            dq.popleft()
        dq.append(now)


def _kills_in_last_hour(runner: str) -> int:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=1)
    with _history_lock:
        dq = _kill_history.get(runner) or deque()
        return sum(1 for t in dq if t >= cutoff)


def _find_instance_id(runner_name: str) -> str | None:
    """Resolve a runner name to an EC2 instance id via the Name tag."""
    if MODE != "production" or not runner_name:
        return None
    try:
        resp = _client("ec2").describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [runner_name, f"*{runner_name}*"]},
                {"Name": "instance-state-name", "Values": ["running"]},
            ]
        )
    except Exception:
        logger.exception("ci_healer: describe_instances failed for %s", runner_name)
        return None
    for res in resp.get("Reservations", []) or []:
        for inst in res.get("Instances", []) or []:
            iid = inst.get("InstanceId")
            if iid:
                return iid
    return None


def _run_ssm(instance_id: str, command: str, timeout_sec: int = 30) -> str:
    """Send a shell command via SSM and return stdout. Empty on failure."""
    try:
        ssm = _client("ssm")
        sent = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]},
            TimeoutSeconds=timeout_sec,
        )
        cmd_id = sent.get("Command", {}).get("CommandId")
        if not cmd_id:
            return ""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(2)
            try:
                inv = ssm.get_command_invocation(
                    CommandId=cmd_id, InstanceId=instance_id)
            except Exception:
                continue
            status = inv.get("Status")
            if status in ("Success", "Failed", "TimedOut", "Cancelled"):
                return inv.get("StandardOutputContent", "") or ""
        return ""
    except Exception:
        logger.exception("ci_healer: SSM %s on %s failed", command, instance_id)
        return ""


def _parse_worker(ps_output: str) -> dict[str, Any] | None:
    """Parse the first Runner.Worker line from `ps` output."""
    for line in ps_output.splitlines():
        if "Runner.Worker" not in line or "grep" in line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        try:
            return {
                "pid": int(parts[0]),
                "cpu_pct": float(parts[1]),
                "elapsed_sec": int(parts[2]),
                "command": parts[3] if len(parts) == 4 else parts[4],
            }
        except (ValueError, IndexError):
            continue
    return None


def heal_hung_ci(incident: dict[str, Any], **_: Any) -> dict[str, Any]:
    """
    Kill a wedged Runner.Worker identified by the given CIIncident payload.
    Expects incident fields: runner_name, job_id, job_name, incident_id.
    """
    runner = (incident or {}).get("runner_name") or ""
    if not runner:
        return {"ok": False, "reason": "no_runner_name"}
    if _kills_in_last_hour(runner) >= MAX_KILLS_PER_HOUR_PER_RUNNER:
        overwatch_graph.record_event(
            event_type="ci_healer_circuit_open",
            service=f"runner:{runner}",
            details={"incident": incident,
                     "kills_last_hour": _kills_in_last_hour(runner)},
            severity="critical",
        )
        return {"ok": False, "reason": "circuit_breaker_open", "runner": runner}

    if MODE != "production":
        return {"ok": True, "mock": True, "runner": runner}

    instance_id = _find_instance_id(runner)
    if not instance_id:
        return {"ok": False, "reason": "instance_not_found", "runner": runner}

    ps = _run_ssm(
        instance_id,
        "ps -eo pid,pcpu,etimes,comm,args --sort=-etimes "
        "| grep Runner.Worker | grep -v grep | head -5",
    )
    worker = _parse_worker(ps)
    if not worker:
        return {"ok": False, "reason": "no_worker_process",
                "runner": runner, "instance_id": instance_id}
    if (worker["elapsed_sec"] < MIN_HUNG_ELAPSED_SEC
            or worker["cpu_pct"] > IDLE_CPU_THRESHOLD_PCT):
        return {"ok": False, "reason": "worker_not_wedged",
                "runner": runner, "worker": worker}

    pid = worker["pid"]
    started = time.time()
    _run_ssm(instance_id, f"kill -TERM {pid}; sleep 10; kill -KILL {pid} 2>/dev/null || true",
             timeout_sec=30)
    _record_kill(runner)
    duration_ms = int((time.time() - started) * 1000)
    overwatch_graph.record_healing_action(
        action_type="ci_kill_hung_worker",
        target=f"{runner}:pid={pid}",
        blast_radius=BLAST_MODERATE,
        trigger=f"ci_heartbeat:{incident.get('incident_id', '')}",
        outcome="success",
        duration_ms=duration_ms,
    )
    return {"ok": True, "runner": runner, "instance_id": instance_id,
            "killed_pid": pid, "worker": worker,
            "kills_last_hour": _kills_in_last_hour(runner)}


registry.register(Capability(
    name="heal_hung_ci",
    function=heal_hung_ci,
    blast_radius=BLAST_MODERATE,
    description=(
        "Kill a wedged Runner.Worker on the self-hosted runner that picked "
        "up a hung CI job. Circuit-broken to 3 kills/hour/runner."
    ),
))

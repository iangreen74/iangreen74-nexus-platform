"""
Runner Health Monitor — keeps CI runners healthy via SSM.

Checks disk (>80% → prune), docker daemon (down → restart),
runner agent (inactive → restart), socket perms (wrong → chmod).
All fixes via SSM — no SSH, no inbound ports. Cached 60s.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from nexus.config import MODE

logger = logging.getLogger(__name__)

RUNNER_NAMES = ["aria-runner-3", "aria-runner-4", "nexus-runner-1"]
DISK_THRESHOLD = 80

# Cache: (results, timestamp)
_cache: tuple[list[dict[str, Any]], float] = ([], 0)
_CACHE_TTL = 60


def check_all_runners(force: bool = False) -> list[dict[str, Any]]:
    """Check all runners and auto-fix issues. Cached 60s."""
    global _cache
    now = time.time()
    if not force and _cache[1] > 0 and (now - _cache[1]) < _CACHE_TTL:
        return _cache[0]

    if MODE != "production":
        results = [{"runner": n, "disk_pct": "42", "docker": "27.5.1",
                    "agent": "active", "actions": [], "healthy": True, "mock": True}
                   for n in RUNNER_NAMES]
        _cache = (results, now)
        return results

    try:
        import boto3
        ec2 = boto3.client("ec2", region_name="us-east-1")
        ssm = boto3.client("ssm", region_name="us-east-1")
    except Exception as exc:
        logger.warning("Cannot connect to AWS: %s", exc)
        return [{"runner": "all", "error": f"AWS connection failed: {exc}"}]

    results = [_check_runner(ssm, iid, name) for iid, name in _get_runner_instances(ec2)]
    healthy = sum(1 for r in results if not r.get("actions") and not r.get("error"))
    logger.info("Runner health: %d/%d healthy", healthy, len(results))
    _cache = (results, now)
    return results


def _get_runner_instances(ec2) -> list[tuple[str, str]]:
    """Return [(instance_id, name_tag), ...] for running runners."""
    instances: list[tuple[str, str]] = []
    try:
        resp = ec2.describe_instances(Filters=[
            {"Name": "tag:Name", "Values": RUNNER_NAMES},
            {"Name": "instance-state-name", "Values": ["running"]},
        ])
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                name = next(
                    (t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"),
                    "",
                )
                instances.append((i["InstanceId"], name))
    except Exception as exc:
        logger.warning("Cannot list runners: %s", exc)
    return instances


def _check_runner(ssm, instance_id: str, name: str) -> dict[str, Any]:
    """Check one runner and auto-fix detected issues."""
    try:
        cmd = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [
                "echo DISK:$(df / --output=pcent | tail -1 | tr -d ' %')",
                "echo DOCKER:$(docker info --format '{{.ServerVersion}}' 2>/dev/null || echo DOWN)",
                "echo AGENT:$(systemctl is-active actions.runner.*.service 2>/dev/null | head -1 || echo UNKNOWN)",
                "echo SOCKET:$(stat -c '%a' /var/run/docker.sock 2>/dev/null || echo MISSING)",
            ]},
            Comment=f"Overwatch health check: {name}",
        )
        time.sleep(8)
        output = ssm.get_command_invocation(
            CommandId=cmd["Command"]["CommandId"],
            InstanceId=instance_id,
        ).get("StandardOutputContent", "")

        disk = _parse(output, "DISK:")
        docker = _parse(output, "DOCKER:")
        agent = _parse(output, "AGENT:")
        socket_perms = _parse(output, "SOCKET:")

        actions: list[str] = []

        # Auto-fix disk pressure
        if disk and disk.isdigit() and int(disk) > DISK_THRESHOLD:
            _fix(ssm, instance_id, [
                "docker system prune -af --volumes",
                "find /tmp -maxdepth 1 -mtime +1 -delete 2>/dev/null || true",
            ])
            actions.append(f"pruned (disk was {disk}%)")

        # Auto-fix docker down
        if docker == "DOWN":
            _fix(ssm, instance_id, ["systemctl restart docker"])
            actions.append("restarted docker")

        # Auto-fix agent inactive
        if agent and agent not in ("active", "UNKNOWN"):
            _fix(ssm, instance_id, ["systemctl restart actions.runner.*.service"])
            actions.append("restarted agent")

        # Auto-fix socket permissions
        if socket_perms and socket_perms not in ("666", "660", "MISSING"):
            _fix(ssm, instance_id, ["chmod 666 /var/run/docker.sock"])
            actions.append("fixed socket perms")

        return {
            "runner": name,
            "disk_pct": disk,
            "docker": docker,
            "agent": agent,
            "socket": socket_perms,
            "actions": actions,
            "healthy": not actions,
        }
    except Exception as exc:
        logger.warning("Check failed for %s: %s", name, exc)
        return {"runner": name, "error": str(exc)[:200], "healthy": False}


def _fix(ssm, instance_id: str, commands: list[str]) -> None:
    """Run fix commands on a runner. Never raises."""
    try:
        ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": commands},
            Comment="Overwatch auto-fix",
        )
    except Exception as exc:
        logger.error("Fix failed on %s: %s", instance_id, exc)


def _parse(output: str, prefix: str) -> str | None:
    """Extract LABEL:VALUE from command output."""
    for line in output.split("\n"):
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip()
    return None


def format_for_report(results: list[dict[str, Any]]) -> str:
    """Format runner health for the diagnostic report."""
    if not results:
        return "RUNNER HEALTH: no data"
    lines = ["RUNNER HEALTH:"]
    for r in results:
        if r.get("error"):
            lines.append(f"  {r['runner']}: ERROR — {r['error']}")
            continue
        status = "healthy" if r.get("healthy") else "fixed"
        disk = r.get("disk_pct", "?")
        docker = r.get("docker", "?")
        agent = r.get("agent", "?")
        lines.append(
            f"  {r['runner']}: {status} | disk {disk}% | docker {docker} | agent {agent}"
        )
        for a in r.get("actions", []):
            lines.append(f"    → {a}")
    return "\n".join(lines)


def get_summary() -> dict[str, Any]:
    """Aggregate summary for API endpoints."""
    results = check_all_runners()
    return {
        "total": len(results),
        "healthy": sum(1 for r in results if r.get("healthy")),
        "fixed": sum(1 for r in results if r.get("actions")),
        "errors": sum(1 for r in results if r.get("error")),
        "runners": results,
    }

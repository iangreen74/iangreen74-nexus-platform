"""
Capability Discovery Sensor — auto-detect what Forgewing can do.

Instead of hardcoding what to monitor, Overwatch discovers Forgewing's
capabilities by probing known API patterns. When a new endpoint appears
(e.g., /smoke-test, /deploy-preview), Overwatch automatically starts
monitoring it without a code change.

Discovery sources:
1. Health endpoint — always available, gives basic liveness
2. API endpoint probing — check known endpoint patterns
3. Deployment features — preview, rollback, self-heal
4. QA features — smoke test, performance, error monitoring

Each discovered capability is tracked in memory and new discoveries
trigger an info-level event in the Overwatch graph.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from nexus.capabilities.forgewing_api import call_api
from nexus.config import MODE

logger = logging.getLogger("nexus.sensors.capability_discovery")


@dataclass
class DiscoveredCapability:
    name: str
    endpoint: str
    method: str = "GET"
    status: str = "unknown"  # available | unavailable | error
    last_checked: str = ""
    response_time_ms: int = 0
    first_seen: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "endpoint": self.endpoint,
            "method": self.method,
            "status": self.status,
            "last_checked": self.last_checked,
            "response_time_ms": self.response_time_ms,
            "first_seen": self.first_seen,
        }


# Known endpoint patterns to probe.
# {tid} is replaced with the first active tenant ID.
ENDPOINT_PATTERNS: list[tuple[str, str, str, str]] = [
    ("health", "GET", "/health", "API liveness"),
    ("deploy_progress", "GET", "/deploy-progress/{tid}", "Deploy status tracking"),
    ("deployment_dna", "GET", "/deployment-dna/{tid}", "Codebase analysis for deploy strategy"),
    ("deploy_preview", "GET", "/deploy-preview/{tid}/0", "Preview deploy status"),
    ("deployment_intelligence", "GET", "/deployment-intelligence", "Cross-tenant deploy patterns"),
    ("smoke_test", "GET", "/smoke-test/{tid}/latest", "Automated smoke test results"),
    ("tenant_status", "GET", "/api/status/{tid}", "Tenant status"),
    ("onboarding_verify", "GET", "/onboarding/verify/{tid}", "Onboarding checklist"),
    ("conversation", "GET", "/conversation/{tid}", "ARIA conversation history"),
    ("tasks", "GET", "/tasks/{tid}", "Tenant tasks"),
]

_discovered: dict[str, DiscoveredCapability] = {}


def discover_capabilities(tenant_ids: list[str] | None = None) -> dict[str, Any]:
    """Probe Forgewing's API endpoints and discover what's available."""
    now = datetime.now(timezone.utc).isoformat()
    results: list[DiscoveredCapability] = []
    new_discoveries: list[str] = []
    probe_tid = (tenant_ids[0] if tenant_ids else "probe-test")

    for name, method, path_template, _desc in ENDPOINT_PATTERNS:
        path = path_template.replace("{tid}", probe_tid)
        start = time.monotonic()
        resp = call_api(method, path, timeout=5)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        status_code = resp.get("status", 0)
        if resp.get("error"):
            if status_code == 404:
                status = "unavailable"
            elif status_code in (401, 403):
                status = "available"  # exists but needs auth
            else:
                status = "error"
        elif resp.get("mock"):
            status = "available"
        else:
            status = "available"

        cap = DiscoveredCapability(
            name=name, endpoint=path_template, method=method,
            status=status, last_checked=now, response_time_ms=elapsed_ms,
        )
        if name not in _discovered and status == "available":
            cap.first_seen = now
            new_discoveries.append(name)
            logger.info("Discovered Forgewing capability: %s (%s)", name, path_template)
        elif name in _discovered:
            cap.first_seen = _discovered[name].first_seen

        _discovered[name] = cap
        results.append(cap)

    if new_discoveries:
        try:
            from nexus import overwatch_graph
            overwatch_graph.record_event(
                "capability_discovered", "forgewing",
                {"new_capabilities": new_discoveries,
                 "total_available": len([r for r in results if r.status == "available"])},
                "info",
            )
        except Exception:
            pass

    available = [r for r in results if r.status == "available"]
    return {
        "total_probed": len(results),
        "available": len(available),
        "unavailable": len([r for r in results if r.status == "unavailable"]),
        "errors": len([r for r in results if r.status == "error"]),
        "new_discoveries": new_discoveries,
        "capabilities": [r.to_dict() for r in results],
        "checked_at": now,
    }


def get_discovered_capabilities() -> list[DiscoveredCapability]:
    return list(_discovered.values())


def get_capability_health() -> dict[str, Any]:
    caps = list(_discovered.values())
    if not caps:
        return {"status": "unknown", "message": "No capabilities discovered yet"}
    available = sum(1 for c in caps if c.status == "available")
    total = len(caps)
    responding = [c for c in caps if c.response_time_ms > 0 and c.status == "available"]
    avg_response = (sum(c.response_time_ms for c in responding) // len(responding)) if responding else 0
    return {
        "status": "healthy" if available == total else "degraded" if available > total // 2 else "critical",
        "available": available,
        "total": total,
        "avg_response_ms": avg_response,
        "slow_endpoints": [c.to_dict() for c in caps if c.response_time_ms > 2000 and c.status == "available"],
    }

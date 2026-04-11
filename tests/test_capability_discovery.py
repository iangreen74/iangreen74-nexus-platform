"""Tests for capability auto-discovery."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.sensors.capability_discovery import (  # noqa: E402
    ENDPOINT_PATTERNS,
    DiscoveredCapability,
    _discovered,
    get_capability_health,
)


def test_endpoint_patterns_exist():
    assert len(ENDPOINT_PATTERNS) >= 8
    names = [p[0] for p in ENDPOINT_PATTERNS]
    assert "health" in names
    assert "deploy_progress" in names
    assert "smoke_test" in names


def test_discovered_capability_to_dict():
    cap = DiscoveredCapability(
        name="health", endpoint="/health", status="available",
        response_time_ms=45, last_checked="2026-04-10",
    )
    d = cap.to_dict()
    assert d["name"] == "health"
    assert d["status"] == "available"


def test_capability_health_empty():
    _discovered.clear()
    health = get_capability_health()
    assert health["status"] == "unknown"


def test_capability_health_all_available():
    _discovered.clear()
    _discovered["health"] = DiscoveredCapability(
        name="health", endpoint="/health", status="available", response_time_ms=50,
    )
    _discovered["tasks"] = DiscoveredCapability(
        name="tasks", endpoint="/tasks/{tid}", status="available", response_time_ms=80,
    )
    health = get_capability_health()
    assert health["status"] == "healthy"
    assert health["available"] == 2


def test_capability_health_degraded():
    _discovered.clear()
    _discovered["health"] = DiscoveredCapability(
        name="health", endpoint="/health", status="available", response_time_ms=50,
    )
    _discovered["tasks"] = DiscoveredCapability(
        name="tasks", endpoint="/tasks", status="available", response_time_ms=80,
    )
    _discovered["broken"] = DiscoveredCapability(
        name="broken", endpoint="/broken", status="error", response_time_ms=0,
    )
    health = get_capability_health()
    assert health["status"] == "degraded"  # 2/3 available > 3//2=1
    _discovered.clear()

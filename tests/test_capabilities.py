"""
Capability registry tests — registration, rate limiting, safety metadata.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.capabilities import alert, ecs_ops  # noqa: F401,E402 — self-register
from nexus.capabilities.registry import (  # noqa: E402
    Capability,
    CapabilityRegistry,
    RateLimitExceeded,
    UnknownCapability,
    registry,
)
from nexus.config import BLAST_DANGEROUS, BLAST_SAFE  # noqa: E402


def test_global_registry_has_expected_capabilities():
    names = {c.name for c in registry.list_all()}
    assert "restart_service" in names
    assert "get_service_logs" in names
    assert "send_telegram_alert" in names
    assert "send_escalation" in names


def test_list_safe_filters_by_blast_radius():
    safe = {c.name for c in registry.list_safe()}
    assert "get_service_logs" in safe  # safe
    assert "restart_service" not in safe  # moderate


def test_unknown_capability_raises():
    with pytest.raises(UnknownCapability):
        registry.get("nope")


def test_execute_records_success():
    reg = CapabilityRegistry(rate_limit_per_hour=5)
    reg.register(
        Capability(
            name="ping",
            function=lambda **kw: {"pong": True, **kw},
            blast_radius=BLAST_SAFE,
            description="test",
        )
    )
    rec = reg.execute("ping", x=1)
    assert rec.ok is True
    assert rec.result == {"pong": True, "x": 1}


def test_execute_records_failure_without_raising():
    reg = CapabilityRegistry(rate_limit_per_hour=5)

    def boom(**_):
        raise RuntimeError("kaboom")

    reg.register(Capability(name="boom", function=boom, blast_radius=BLAST_SAFE, description=""))
    rec = reg.execute("boom")
    assert rec.ok is False
    assert "kaboom" in (rec.error or "")


def test_rate_limit_enforced():
    reg = CapabilityRegistry(rate_limit_per_hour=2)
    reg.register(
        Capability(name="noop", function=lambda **kw: 1, blast_radius=BLAST_SAFE, description="")
    )
    reg.execute("noop")
    reg.execute("noop")
    with pytest.raises(RateLimitExceeded):
        reg.execute("noop")


def test_invalid_blast_radius_rejected():
    reg = CapabilityRegistry()
    with pytest.raises(ValueError):
        reg.register(
            Capability(name="x", function=lambda: None, blast_radius="weird", description="")
        )


def test_dangerous_capability_allowed_but_flagged():
    reg = CapabilityRegistry()
    reg.register(
        Capability(
            name="nuke",
            function=lambda **kw: "ok",
            blast_radius=BLAST_DANGEROUS,
            description="test",
            requires_approval=True,
        )
    )
    cap = reg.get("nuke")
    assert cap.requires_approval is True
    assert cap not in reg.list_safe()

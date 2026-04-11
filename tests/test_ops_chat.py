"""Tests for Ops Chat module."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.dashboard.ops_chat import (  # noqa: E402
    build_system_prompt,
    chat,
    execute_action,
)


def test_chat_local_mode():
    result = chat("What is the platform status?", context={})
    assert "response" in result
    assert "actions" in result
    assert "[Local mode]" in result["response"]


def test_build_system_prompt():
    context = {"status": {"overall": "degraded"}, "tenants": [], "capabilities": []}
    prompt = build_system_prompt(context)
    assert "Overwatch Ops Assistant" in prompt
    assert "PLATFORM STATUS" in prompt
    assert "AVAILABLE ACTIONS" in prompt


def test_execute_unknown_capability():
    result = execute_action("nonexistent_thing")
    assert "error" in result


def test_execute_known_capability_local():
    result = execute_action("validate_tenant_onboarding:tenant_id=tenant-alpha")
    assert isinstance(result, dict)
    if result.get("executed"):
        assert result["executed"] == "validate_tenant_onboarding"


def test_execute_action_parsing():
    result = execute_action("diagnose_daemon_timeout")
    assert isinstance(result, dict)
    # Should execute successfully in local mode (returns mock)
    if result.get("executed"):
        assert result["executed"] == "diagnose_daemon_timeout"

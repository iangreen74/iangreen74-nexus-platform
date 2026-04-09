"""
Alerting Capabilities.

Thin wrappers over nexus.telegram that format escalations in the
shape Ian expects, and that are registered with the capability
registry so every alert counts against the rate limit too.
"""
from __future__ import annotations

from typing import Any

from nexus.capabilities.registry import Capability, registry
from nexus.config import BLAST_SAFE
from nexus.telegram import send_alert


def send_telegram_alert(message: str, level: str = "info") -> dict[str, Any]:
    """Send an arbitrary alert to the NEXUS Telegram chat."""
    ok = send_alert(message, level=level)  # type: ignore[arg-type]
    return {"sent": ok, "level": level, "message": message}


def send_escalation(event: str, diagnosis: str, suggested_action: str) -> dict[str, Any]:
    """Format and send an escalation message to the operator."""
    body = (
        "*NEXUS Escalation*\n"
        f"*Event:* {event}\n"
        f"*Diagnosis:* {diagnosis}\n"
        f"*Suggested action:* {suggested_action}"
    )
    ok = send_alert(body, level="critical")
    return {
        "sent": ok,
        "event": event,
        "diagnosis": diagnosis,
        "suggested_action": suggested_action,
    }


registry.register(
    Capability(
        name="send_telegram_alert",
        function=send_telegram_alert,
        blast_radius=BLAST_SAFE,
        description="Send a Telegram alert to the NEXUS chat.",
    )
)

registry.register(
    Capability(
        name="send_escalation",
        function=send_escalation,
        blast_radius=BLAST_SAFE,
        description="Send a formatted escalation with diagnosis + suggested action.",
    )
)

"""
Alert Dispatcher — fire Telegram alerts on critical triage events with dedup.

This is the bridge between Overwatch's reasoning layer and the operator's
phone. The rules:

- Only `critical` and high-confidence `escalate_*` decisions get alerted.
- Identical alerts are deduplicated for ALERT_COOLDOWN_SECONDS so we don't
  spam Ian when the dashboard re-polls every 30 seconds.
- Failures are swallowed — alerting must never crash the control plane.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from nexus.capabilities.registry import RateLimitExceeded, registry
from nexus.reasoning.triage import TriageDecision

logger = logging.getLogger("nexus.alerts")

# How long the same alert key is suppressed after firing. Long enough to
# survive dashboard polling cadence but short enough that real ongoing
# incidents re-fire eventually.
ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour

# Triage actions that we consider escalation-worthy.
_ESCALATION_ACTIONS = {
    "escalate_to_operator",
    "escalate_with_diagnosis",
}

_lock = threading.Lock()
_last_fired: dict[str, float] = {}


def _should_fire(key: str) -> bool:
    """True if `key` hasn't been fired within the cooldown window."""
    now = time.monotonic()
    with _lock:
        last = _last_fired.get(key)
        if last is not None and (now - last) < ALERT_COOLDOWN_SECONDS:
            return False
        _last_fired[key] = now
        return True


def _is_alert_worthy(decision: TriageDecision) -> bool:
    """Only alert for things that genuinely need human action."""
    if decision.action == "noop":
        return False
    if decision.action == "monitor":
        return False  # monitoring is not actionable
    if decision.action in _ESCALATION_ACTIONS:
        # Only escalate if confidence is high enough to be meaningful
        return decision.confidence >= 0.7
    if decision.blast_radius == "dangerous":
        return True
    return False


def _format(source: str, decision: TriageDecision, context: dict[str, Any] | None) -> str:
    """Build a markdown-flavored alert body."""
    meta = decision.metadata or {}
    diagnosis = meta.get("diagnosis")
    resolution = meta.get("resolution")
    lines = [
        f"*Overwatch escalation* — {source}",
        f"*Action:* `{decision.action}`",
        f"*Blast radius:* {decision.blast_radius}",
        f"*Confidence:* {decision.confidence:.0%}",
        f"*Reasoning:* {decision.reasoning}",
    ]
    if diagnosis:
        lines.append(f"*Diagnosis:* {diagnosis}")
    if resolution:
        lines.append(f"*Resolution:* {resolution}")
    if context:
        bits = ", ".join(f"{k}={v}" for k, v in context.items() if v is not None)
        if bits:
            lines.append(f"*Context:* {bits}")
    return "\n".join(lines)


def maybe_alert(
    source: str,
    decision: TriageDecision,
    *,
    dedup_key: str | None = None,
    context: dict[str, Any] | None = None,
) -> bool:
    """
    Fire a Telegram alert IF the decision is escalation-worthy AND the
    dedup key hasn't been fired in the cooldown window.

    Returns True if an alert was actually sent. Never raises.
    """
    try:
        if not _is_alert_worthy(decision):
            return False
        # Don't alert while a heal chain is still working the problem
        if context and context.get("heal_chain_active"):
            return False
        key = dedup_key or f"{source}:{decision.action}"
        if not _should_fire(key):
            logger.debug("alert suppressed (cooldown): %s", key)
            return False
        message = _format(source, decision, context)
        # Route through the capability registry so alerts are also
        # rate-limited and recorded in the actions history.
        registry.execute("send_telegram_alert", message=message, level="critical")
        return True
    except RateLimitExceeded:
        logger.warning("alert dropped: registry rate limit hit")
        return False
    except Exception:
        logger.exception("maybe_alert failed for %s/%s", source, decision.action)
        return False


def reset_dedup() -> None:
    """Test hook — clear the dedup map."""
    with _lock:
        _last_fired.clear()

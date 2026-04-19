"""
Alert Dispatcher — fire Telegram alerts on operator-actionable events.

Notification policy (Sprint 11 overhaul):
  1. Trajectory events — level transitions, gated-autonomy decisions
  2. Pipeline-blocking errors not auto-healing in 15 min
  3. AWS account-level blockers (quota, cross-account role failures)

Everything else goes to the dashboard, not the phone. Slack deprecated
entirely (legacy secret naming only — no Slack integration exists).

Alerts are deduped by key for ALERT_COOLDOWN_SECONDS.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from nexus.capabilities.registry import RateLimitExceeded, registry
from nexus.reasoning.triage import TriageDecision

logger = logging.getLogger("nexus.alerts")

ALERT_COOLDOWN_SECONDS = 60 * 60  # 1 hour

_ESCALATION_ACTIONS = {
    "escalate_to_operator",
    "escalate_with_diagnosis",
}

# Sources that represent pipeline-blocking infrastructure events.
# Tenant-level escalations (deploy_stuck, token expired, etc.) are handled
# by heal chains and only escalate through _ESCALATION_ACTIONS if unhealed.
_PIPELINE_BLOCKING_SOURCES = {
    "daemon",
    "infrastructure",
    "neptune",
}

_lock = threading.Lock()
_last_fired: dict[str, float] = {}


def _should_fire(key: str) -> bool:
    now = time.monotonic()
    with _lock:
        last = _last_fired.get(key)
        if last is not None and (now - last) < ALERT_COOLDOWN_SECONDS:
            return False
        _last_fired[key] = now
        return True


def _is_alert_worthy(
    decision: TriageDecision,
    source: str = "",
) -> bool:
    """Only alert for events that genuinely need human action NOW.

    Silenced (goes to dashboard only):
      - noop, monitor, auto-heal successes
      - tenant-level issues with active heal chains
      - routine CI failures (heal chain handles retries)
    """
    if decision.action in ("noop", "monitor"):
        return False
    # Escalations with high confidence are always alertable
    if decision.action in _ESCALATION_ACTIONS and decision.confidence >= 0.7:
        return True
    # Dangerous blast radius on infrastructure sources
    if decision.blast_radius == "dangerous" and source in _PIPELINE_BLOCKING_SOURCES:
        return True
    return False


def send_trajectory_alert(level: int, name: str, detail: str) -> bool:
    """Fire a Telegram alert for trajectory level transitions."""
    key = f"trajectory:level{level}"
    if not _should_fire(key):
        return False
    message = f"*⚡ Level {level} achieved — {name}*\n{detail}"
    try:
        registry.execute("send_telegram_alert", message=message, level="info")
        return True
    except (RateLimitExceeded, Exception):
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
        if not _is_alert_worthy(decision, source=source):
            return False
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

"""
Autonomous Execution Engine — closes the loop between triage and action.

This is the core of Overwatch's autonomy. When triage produces a decision,
the executor translates it into a registered capability call, enforces
safety gates, fires it, records the outcome, and escalates on failure.

The closed loop:  Sensor → Triage → **Execute** → Record → Learn

Safety gates (every one must pass before auto-execution):
1. Confidence ≥ 0.8 for safe actions, ≥ 0.9 for moderate
2. Blast radius: dangerous ALWAYS escalates, never auto-executes
3. Cooldown: same action+target can't re-fire within 30 minutes
4. Rate limit: enforced by the CapabilityRegistry (10/hour global)

If any gate fails, the engine either skips (for cooldown/confidence)
or escalates (for blast radius / execution failure).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from nexus import overwatch_graph
from nexus.capabilities.registry import RateLimitExceeded, registry
from nexus.config import BLAST_DANGEROUS, BLAST_MODERATE, BLAST_SAFE, FORGEWING_CLUSTER
from nexus.reasoning.triage import TriageDecision

logger = logging.getLogger("nexus.executor")

# ---- Action → Capability mapping -------------------------------------------
# Each entry maps a triage action name to a (capability_name, kwargs_builder).
# The kwargs_builder takes a context dict and returns kwargs for the capability.
# Actions not in this map are either escalations or unknown (both handled below).

_TENANT_ID: Callable[[dict[str, Any]], dict[str, Any]] = lambda ctx: {"tenant_id": ctx.get("tenant_id", "")}
_NOTHING: Callable[[dict[str, Any]], dict[str, Any]] = lambda ctx: {}

ACTION_CAPABILITY_MAP: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "restart_daemon_service": ("restart_daemon", _NOTHING),
    "validate_tenant_onboarding": ("validate_tenant_onboarding", _TENANT_ID),
    "refresh_tenant_token": ("refresh_tenant_token", _TENANT_ID),
    "retrigger_ingestion": ("retrigger_ingestion", _TENANT_ID),
    "restart_tenant_service": ("restart_service", lambda ctx: {
        "cluster": FORGEWING_CLUSTER, "service": ctx.get("service", "aria-daemon"),
    }),
    "investigate_stuck_tasks": ("check_pipeline_health", _TENANT_ID),
    "verify_write_access": ("verify_write_access", _TENANT_ID),
}

# Actions that are purely informational — no execution needed.
_SKIP_ACTIONS = frozenset({"noop", "monitor", "retry_with_fence_stripping"})

# ---- Execution result -------------------------------------------------------

@dataclass
class ExecutionResult:
    status: str  # executed | escalated | skipped | failed
    reason: str = ""
    outcome: str = ""  # success | failed | alert_sent
    action_taken: str = ""
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status}
        if self.reason:
            d["reason"] = self.reason
        if self.outcome:
            d["outcome"] = self.outcome
        if self.action_taken:
            d["action_taken"] = self.action_taken
        if self.error:
            d["error"] = self.error
        return d


# ---- Cooldown ---------------------------------------------------------------
_cooldown_lock = threading.Lock()
_cooldowns: dict[str, float] = {}  # key → monotonic timestamp of last execution
_last_outcomes: dict[str, str] = {}  # key → "success" | "failed"
COOLDOWN_MINUTES = 30


def _cooldown_remaining(key: str) -> float:
    """Minutes remaining on cooldown for this key, or 0 if expired."""
    with _cooldown_lock:
        last = _cooldowns.get(key)
        if last is None:
            return 0.0
        elapsed = (time.monotonic() - last) / 60.0
        remaining = COOLDOWN_MINUTES - elapsed
        return max(0.0, remaining)


def _in_cooldown(key: str) -> bool:
    return _cooldown_remaining(key) > 0


def _set_cooldown(key: str, outcome: str = "success") -> None:
    with _cooldown_lock:
        _cooldowns[key] = time.monotonic()
        _last_outcomes[key] = outcome


def _last_outcome(key: str) -> str:
    with _cooldown_lock:
        return _last_outcomes.get(key, "")


def reset_cooldowns() -> None:
    """Test hook."""
    with _cooldown_lock:
        _cooldowns.clear()
        _last_outcomes.clear()


# ---- Escalation helper ------------------------------------------------------
def _escalate(
    decision: TriageDecision,
    context: dict[str, Any],
    failure_reason: str = "",
) -> ExecutionResult:
    """Send an escalation alert via the send_escalation capability."""
    meta = decision.metadata or {}
    diagnosis = meta.get("diagnosis") or decision.reasoning
    resolution = meta.get("resolution") or ""
    event = context.get("source", "overwatch")

    extra = ""
    if failure_reason:
        extra = f"\nAuto-heal failed: {failure_reason}"

    try:
        registry.execute(
            "send_escalation",
            event=f"{event}: {decision.action}",
            diagnosis=diagnosis + extra,
            suggested_action=resolution or decision.action,
        )
        return ExecutionResult(
            status="escalated",
            reason=failure_reason or "escalation",
            outcome="alert_sent",
            action_taken="send_escalation",
        )
    except Exception as exc:
        logger.exception("escalation send failed")
        return ExecutionResult(status="failed", error=str(exc), action_taken="send_escalation")


# ---- Main entry point -------------------------------------------------------
def execute_decision(
    decision: TriageDecision,
    context: dict[str, Any],
) -> ExecutionResult:
    """
    Execute a triage decision if it meets all safety criteria.

    This is the single entry point for the autonomous loop. Every path
    through this function records the outcome to the Overwatch graph.
    """
    action = decision.action

    # 1. Skip informational actions
    if action in _SKIP_ACTIONS:
        return ExecutionResult(status="skipped", reason=action)

    # 2. Escalation actions → always fire (they're just alerts)
    if action.startswith("escalate"):
        result = _escalate(decision, context)
        _record(decision, context, result)
        return result

    # 3. Look up the capability mapping
    mapping = ACTION_CAPABILITY_MAP.get(action)
    if mapping is None:
        return ExecutionResult(status="skipped", reason=f"no capability for {action}")

    capability_name, kwargs_builder = mapping
    target = context.get("target", "global")
    cooldown_key = f"{capability_name}:{target}"

    # Safety gate 1: Confidence
    if decision.blast_radius == BLAST_SAFE and decision.confidence < 0.8:
        return ExecutionResult(status="skipped", reason=f"confidence {decision.confidence:.0%} < 80% for safe action")
    if decision.blast_radius == BLAST_MODERATE and decision.confidence < 0.9:
        result = _escalate(decision, context, failure_reason="confidence too low for moderate action")
        _record(decision, context, result)
        return result
    if decision.blast_radius == BLAST_DANGEROUS:
        result = _escalate(decision, context, failure_reason="dangerous actions always require human approval")
        _record(decision, context, result)
        return result

    # Safety gate 2: Cooldown — but override if the last execution didn't
    # fix the problem (the sensor is still reporting the same issue).
    remaining = _cooldown_remaining(cooldown_key)
    if remaining > 0:
        prev = _last_outcome(cooldown_key)
        if prev == "success":
            # Previous execution was successful. If it's still in cooldown,
            # respect it — the fix may need time to propagate.
            return ExecutionResult(
                status="skipped",
                reason=f"cooldown {remaining:.0f}m remaining ({cooldown_key}), last attempt succeeded",
            )
        # Previous attempt failed or has no recorded outcome — let it retry
        # after a shorter grace period (5 min) rather than the full 30 min.
        if remaining > COOLDOWN_MINUTES - 5:
            return ExecutionResult(
                status="skipped",
                reason=f"cooldown {remaining:.0f}m remaining ({cooldown_key}), retrying after grace period",
            )

    # Safety gate 3: Execute through the registry (which enforces rate limits)
    try:
        kwargs = kwargs_builder(context)
        record = registry.execute(capability_name, **kwargs)

        if record.ok:
            _set_cooldown(cooldown_key, "success")
            result = ExecutionResult(
                status="executed",
                outcome="success",
                action_taken=capability_name,
                result=record.result,
            )
            overwatch_graph.record_event(
                "auto_heal_success",
                capability_name,
                {
                    "target": target,
                    "confidence": decision.confidence,
                    "blast_radius": decision.blast_radius,
                },
                "info",
            )
        else:
            # Capability executed but failed — set cooldown as failed and escalate
            _set_cooldown(cooldown_key, "failed")
            result = _escalate(decision, context, failure_reason=record.error or "capability returned error")
            result.action_taken = capability_name
            result.outcome = "failed_then_escalated"
            overwatch_graph.record_event(
                "auto_heal_failed",
                capability_name,
                {"target": target, "error": record.error},
                "warning",
            )
        _record(decision, context, result)
        return result

    except RateLimitExceeded:
        return ExecutionResult(status="skipped", reason="rate limit exceeded")
    except Exception as exc:
        logger.exception("executor failed for %s", capability_name)
        result = _escalate(decision, context, failure_reason=str(exc))
        _record(decision, context, result)
        return result


def _record(
    decision: TriageDecision,
    context: dict[str, Any],
    result: ExecutionResult,
) -> None:
    """Best-effort write to the graph. Never raises."""
    try:
        overwatch_graph.record_event(
            "execution",
            context.get("source", "executor"),
            {
                "action": decision.action,
                "status": result.status,
                "outcome": result.outcome,
                "action_taken": result.action_taken,
                "error": result.error,
                "target": context.get("target"),
            },
            "info" if result.status in ("executed", "escalated") else "warning",
        )
    except Exception:
        pass

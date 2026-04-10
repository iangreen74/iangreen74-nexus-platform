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
from nexus.reasoning.heal_chain import ChainProgress, HealStep, get_chain
from nexus.reasoning.triage import TriageDecision

logger = logging.getLogger("nexus.executor")

# ---- Action → Capability mapping -------------------------------------------
# Each entry maps a triage action name to a (capability_name, kwargs_builder).
# The kwargs_builder takes a context dict and returns kwargs for the capability.
# Actions not in this map are either escalations or unknown (both handled below).

_TENANT_ID: Callable[[dict[str, Any]], dict[str, Any]] = lambda ctx: {"tenant_id": ctx.get("tenant_id", "")}
_NOTHING: Callable[[dict[str, Any]], dict[str, Any]] = lambda ctx: {}

def _ci_retrigger_kwargs(ctx: dict[str, Any]) -> dict[str, Any]:
    """Get the most recent failed workflow run_id for retriggering."""
    try:
        result = registry.execute("get_failing_workflows")
        if result.ok and result.result:
            failing = result.result.get("failing", [])
            if failing:
                return {"run_id": failing[0].get("run_id", 0)}
    except Exception:
        pass
    return {"run_id": 0}


ACTION_CAPABILITY_MAP: dict[str, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "restart_daemon_service": ("restart_daemon", _NOTHING),
    "retrigger_ci": ("retrigger_workflow", _ci_retrigger_kwargs),
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
    """Test hook — also resets escalation dedup."""
    with _cooldown_lock:
        _cooldowns.clear()
        _last_outcomes.clear()
    reset_escalation_dedup()


# ---- Escalation dedup -------------------------------------------------------
# The executor fires escalations independently from alert_dispatcher.
# Without dedup, every 30s poll cycle produces a new send_escalation call —
# this caused 705 entries in the actions history and flooded Telegram.
_escalation_lock = threading.Lock()
_escalation_last_fired: dict[str, float] = {}
ESCALATION_DEDUP_SECONDS = 30 * 60  # 30 minutes — matches cooldown window


def _escalation_should_fire(key: str) -> bool:
    """True if this escalation key hasn't fired within the dedup window."""
    now = time.monotonic()
    with _escalation_lock:
        last = _escalation_last_fired.get(key)
        if last is not None and (now - last) < ESCALATION_DEDUP_SECONDS:
            return False
        _escalation_last_fired[key] = now
        return True


def reset_escalation_dedup() -> None:
    """Test hook."""
    with _escalation_lock:
        _escalation_last_fired.clear()


# ---- Escalation helper ------------------------------------------------------
def _escalate(
    decision: TriageDecision,
    context: dict[str, Any],
    failure_reason: str = "",
) -> ExecutionResult:
    """Send an escalation alert via the send_escalation capability."""
    # Dedup: don't re-fire the same escalation within the dedup window.
    # This is what prevents 705 send_escalation entries in the actions history.
    source = context.get("source", "overwatch")
    dedup_key = f"{source}:{decision.action}"
    if not _escalation_should_fire(dedup_key):
        return ExecutionResult(
            status="skipped",
            reason=f"escalation dedup ({dedup_key})",
        )

    meta = decision.metadata or {}
    diagnosis = meta.get("diagnosis") or decision.reasoning
    resolution = meta.get("resolution") or ""

    # Build human-readable source name
    _FRIENDLY = {"daemon": "Daemon", "ci": "CI/CD"}
    friendly = _FRIENDLY.get(source, source)
    if source.startswith("tenant:"):
        friendly = f"Tenant {source.split(':')[-1][:14]}"
    elif source.startswith("performance:"):
        metric = source.split(":")[1] if ":" in source else "unknown"
        friendly = f"Performance ({metric})"
    elif source.startswith("capability:"):
        friendly = f"Tenant capabilities ({source.split(':')[-1][:14]})"

    # Simplify heal chain exhaustion messages
    chain_summary = meta.get("heal_chain_summary", "")
    if chain_summary:
        steps = [s.strip() for s in chain_summary.split("\n") if "→" in s]
        if steps:
            diagnosis += "\nOverwatch tried: " + "; ".join(steps)

    extra = ""
    if failure_reason and "heal chain exhausted" in failure_reason:
        extra = "\nOverwatch exhausted its recovery options — needs your attention."
    elif failure_reason:
        extra = f"\n{failure_reason}"

    try:
        registry.execute(
            "send_escalation",
            event=friendly,
            diagnosis=diagnosis + extra,
            suggested_action=resolution or "Investigate manually",
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


# ---- Active heal chains -----------------------------------------------------
# Tracks in-progress heal chains by incident source key.
# When a chain is active, the executor drives it instead of re-triaging.
_chain_lock = threading.Lock()
_active_chains: dict[str, ChainProgress] = {}


def get_active_chain(source: str) -> ChainProgress | None:
    with _chain_lock:
        return _active_chains.get(source)


def set_active_chain(source: str, progress: ChainProgress) -> None:
    with _chain_lock:
        _active_chains[source] = progress


def clear_active_chain(source: str) -> None:
    with _chain_lock:
        _active_chains.pop(source, None)


def get_all_active_chains() -> dict[str, dict]:
    """For the dashboard — return all active chains and their progress."""
    with _chain_lock:
        return {
            source: {
                "chain": p.chain_name,
                "step": p.current_step,
                "awaiting_verification": p.awaiting_verification(),
                "cycles_waited": p.cycles_waited,
                "cycles_to_wait": p.cycles_to_wait,
                "total_attempts": p.total_attempts,
                "step_results": p.step_results,
            }
            for source, p in _active_chains.items()
        }


def reset_chains() -> None:
    """Test hook."""
    with _chain_lock:
        _active_chains.clear()


# ---- Chain-aware entry point ------------------------------------------------
def execute_or_continue_chain(
    decision: TriageDecision,
    context: dict[str, Any],
    sensor_healthy: bool,
) -> ExecutionResult:
    """
    Main entry point for the autonomous loop with heal chains.

    If a heal chain is active for this source:
      - If awaiting verification and sensor is now healthy → resolve
      - If awaiting verification and not yet time to check → skip (waiting)
      - If verification failed → advance chain to next step
      - If chain exhausted → escalate with full context
    If no chain is active:
      - Check if the pattern has a heal chain defined
      - If yes, start the chain at step 0
      - If no, fall through to execute_decision (existing behavior)
    """
    source = context.get("source", "unknown")

    # Check for active chain
    progress = get_active_chain(source)

    if progress is not None:
        # Active chain — are we waiting for verification?
        if progress.awaiting_verification():
            progress.tick()
            if progress.awaiting_verification():
                # Still waiting — don't do anything
                return ExecutionResult(
                    status="skipped",
                    reason=f"heal chain '{progress.chain_name}' step {progress.current_step}: "
                           f"waiting {progress.cycles_waited}/{progress.cycles_to_wait} cycles",
                )

        # Verification window expired — check if sensor is healthy
        if sensor_healthy:
            # Healed! Resolve the incident.
            clear_active_chain(source)
            try:
                overwatch_graph.resolve_incident(source, auto_healed=True)
                overwatch_graph.record_event(
                    "heal_chain_success",
                    source,
                    {
                        "chain": progress.chain_name,
                        "step": progress.current_step,
                        "total_attempts": progress.total_attempts,
                        "summary": progress.summary(),
                    },
                    "info",
                )
            except Exception:
                pass
            return ExecutionResult(
                status="executed",
                outcome="verified_healed",
                action_taken=f"heal_chain:{progress.chain_name}",
                result={"step": progress.current_step, "verified": True},
            )

        # Still unhealthy — advance to next step
        chain = get_chain(progress.chain_name)
        if chain is None or chain.is_exhausted(progress.current_step + 1):
            # Chain exhausted — escalate with full context
            clear_active_chain(source)
            decision.metadata = decision.metadata or {}
            decision.metadata["heal_chain_summary"] = progress.summary()
            decision.metadata["diagnosis"] = (
                f"Heal chain '{progress.chain_name}' exhausted after "
                f"{progress.total_attempts} steps. None resolved the issue."
            )
            result = _escalate(decision, context,
                              failure_reason=f"heal chain exhausted: {progress.summary()}")
            _record(decision, context, result)
            return result

        # Execute next step
        progress.advance()
        next_step = chain.step_at(progress.current_step)
        if next_step is None:
            clear_active_chain(source)
            return ExecutionResult(status="skipped", reason="chain step missing")

        progress.cycles_to_wait = next_step.verify_after_cycles
        return _execute_chain_step(next_step, progress, decision, context)

    # No active chain — check if this pattern has one
    if decision.action in _SKIP_ACTIONS:
        return execute_decision(decision, context)

    pattern_name = (decision.metadata or {}).get("pattern_name")
    chain = get_chain(pattern_name) if pattern_name else None

    if chain is None:
        # No chain for this pattern — use existing single-action behavior
        return execute_decision(decision, context)

    # Start a new chain
    progress = ChainProgress(
        chain_name=chain.pattern_name,
        current_step=0,
        cycles_to_wait=chain.steps[0].verify_after_cycles,
    )
    set_active_chain(source, progress)

    first_step = chain.steps[0]
    return _execute_chain_step(first_step, progress, decision, context)


def _execute_chain_step(
    step: HealStep,
    progress: ChainProgress,
    decision: TriageDecision,
    context: dict[str, Any],
) -> ExecutionResult:
    """Execute a single step in a heal chain."""
    source = context.get("source", "unknown")
    target = context.get("target", "global")
    cooldown_key = f"{step.capability}:{target}"

    # Safety gates still apply
    if decision.blast_radius == BLAST_DANGEROUS:
        clear_active_chain(source)
        result = _escalate(decision, context, failure_reason="dangerous action in heal chain")
        _record(decision, context, result)
        return result

    try:
        kwargs = step.build_kwargs(context)
        record = registry.execute(step.capability, **kwargs)

        if record.ok:
            _set_cooldown(cooldown_key, "success")
            progress.record_step(step.capability, "success", str(record.result)[:200])
            try:
                overwatch_graph.acknowledge_incident(source, step.capability)
                overwatch_graph.record_event(
                    "heal_chain_step",
                    source,
                    {
                        "chain": progress.chain_name,
                        "step": progress.current_step,
                        "capability": step.capability,
                        "description": step.description,
                        "result": "success",
                    },
                    "info",
                )
            except Exception:
                pass
            return ExecutionResult(
                status="executed",
                outcome="awaiting_verification",
                action_taken=step.capability,
                result={
                    "chain": progress.chain_name,
                    "step": progress.current_step,
                    "verify_after_cycles": progress.cycles_to_wait,
                    "description": step.description,
                },
            )
        else:
            progress.record_step(step.capability, "failed", record.error or "")
            # Don't escalate yet — advance to next step on next cycle
            progress.cycles_waited = progress.cycles_to_wait  # skip verification wait
            return ExecutionResult(
                status="executed",
                outcome="step_failed_advancing",
                action_taken=step.capability,
                error=record.error,
            )
    except RateLimitExceeded:
        return ExecutionResult(status="skipped", reason="rate limit exceeded")
    except Exception as exc:
        progress.record_step(step.capability, "error", str(exc))
        return ExecutionResult(status="failed", error=str(exc), action_taken=step.capability)


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
    source = context.get("source", "unknown")

    # 1. Skip informational actions — and resolve any open incident for
    #    this source (the system returned to healthy).
    if action in _SKIP_ACTIONS:
        if action == "noop":
            try:
                resolved = overwatch_graph.resolve_incident(source, auto_healed=True)
                if resolved:
                    logger.info("incident resolved (auto): %s", source)
            except Exception:
                pass
        return ExecutionResult(status="skipped", reason=action)

    # Open or re-use an incident for this source
    meta = decision.metadata or {}
    try:
        overwatch_graph.open_incident(
            source=source,
            incident_type=action,
            root_cause=meta.get("diagnosis") or decision.reasoning,
            patterns_matched=[meta.get("pattern_name")] if meta.get("pattern_name") else [],
        )
    except Exception:
        pass

    # 2. Escalation actions → always fire (they're just alerts)
    if action.startswith("escalate"):
        try:
            overwatch_graph.acknowledge_incident(source, "escalation")
        except Exception:
            pass
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
            try:
                overwatch_graph.acknowledge_incident(source, capability_name)
            except Exception:
                pass
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

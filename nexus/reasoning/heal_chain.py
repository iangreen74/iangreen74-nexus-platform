"""
Heal Chains — multi-step healing with verification between steps.

A senior SRE doesn't restart a service and walk away. They restart,
wait, verify, and if it's still broken, try the next thing. Heal
chains encode this behavior: ordered sequences of capabilities with
verification gates between each step.

The executor drives the chain. After each step:
  1. Execute the capability
  2. Tag the incident as awaiting_verification
  3. Wait N poll cycles
  4. Re-check the sensor
  5. If healthy → resolve. If not → advance to next step.
  6. If chain exhausted → escalate with full context from all steps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class HealStep:
    capability: str              # registered capability name
    description: str             # human-readable ("Restart daemon")
    verify_after_cycles: int = 2 # poll cycles to wait before verifying (2 = ~60s)
    kwargs_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    def build_kwargs(self, context: dict[str, Any]) -> dict[str, Any]:
        if self.kwargs_builder:
            return self.kwargs_builder(context)
        return {}


@dataclass
class HealChain:
    pattern_name: str
    steps: list[HealStep]
    max_total_attempts: int = 3  # safety cap across all steps

    def step_at(self, index: int) -> HealStep | None:
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def is_exhausted(self, index: int) -> bool:
        return index >= len(self.steps)


@dataclass
class ChainProgress:
    """Tracks where we are in a heal chain for an active incident."""
    chain_name: str
    current_step: int = 0
    cycles_waited: int = 0
    cycles_to_wait: int = 2
    step_results: list[dict[str, Any]] = field(default_factory=list)
    total_attempts: int = 0

    def awaiting_verification(self) -> bool:
        return self.cycles_waited < self.cycles_to_wait

    def tick(self) -> None:
        """Called each poll cycle while waiting for verification."""
        self.cycles_waited += 1

    def advance(self) -> None:
        """Move to the next step in the chain."""
        self.current_step += 1
        self.cycles_waited = 0

    def record_step(self, capability: str, result: str, detail: str = "") -> None:
        self.step_results.append({
            "step": self.current_step,
            "capability": capability,
            "result": result,
            "detail": detail,
        })
        self.total_attempts += 1

    def summary(self) -> str:
        """Human-readable summary of all steps taken so far."""
        lines = []
        for sr in self.step_results:
            lines.append(f"Step {sr['step']}: {sr['capability']} → {sr['result']}")
            if sr.get("detail"):
                lines.append(f"  {sr['detail']}")
        return "\n".join(lines) if lines else "No steps executed yet"


# ---------------------------------------------------------------------------
# Chain definitions — one per healable pattern
# ---------------------------------------------------------------------------

def _nothing(ctx: dict[str, Any]) -> dict[str, Any]:
    return {}

def _tenant_id(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"tenant_id": ctx.get("tenant_id", "")}

def _ci_retrigger(ctx: dict[str, Any]) -> dict[str, Any]:
    """Look up the most recent failed run_id for retrigger."""
    try:
        from nexus.capabilities.registry import registry
        result = registry.execute("get_failing_workflows")
        if result.ok and result.result:
            failing = result.result.get("failing", [])
            if failing:
                return {"run_id": failing[0].get("run_id", 0)}
    except Exception:
        pass
    return {"run_id": 0}


CHAINS: dict[str, HealChain] = {
    "daemon_stale": HealChain(
        pattern_name="daemon_stale",
        steps=[
            HealStep(
                capability="restart_daemon",
                description="Force new ECS deployment of aria-daemon",
                verify_after_cycles=2,  # ~60s for ECS to roll
            ),
            HealStep(
                capability="diagnose_daemon_timeout",
                description="Analyze logs to identify which hook is hanging",
                verify_after_cycles=1,
            ),
            HealStep(
                capability="check_daemon_code_version",
                description="Check if daemon is running old code",
                verify_after_cycles=1,
            ),
            # If all 3 fail, executor escalates with full context
        ],
    ),

    "ci_failing": HealChain(
        pattern_name="ci_failing",
        steps=[
            HealStep(
                capability="retrigger_workflow",
                description="Retrigger the most recent failed workflow run",
                verify_after_cycles=5,  # ~2.5 min for CI to complete
                kwargs_builder=_ci_retrigger,
            ),
            # If retrigger didn't fix it, escalate with the specific failure
        ],
    ),

    "empty_tenant_token": HealChain(
        pattern_name="empty_tenant_token",
        steps=[
            HealStep(
                capability="refresh_tenant_token",
                description="Mint fresh GitHub token from installation_id",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="validate_tenant_onboarding",
                description="Verify token is now present and working",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),

    "missing_repo_files": HealChain(
        pattern_name="missing_repo_files",
        steps=[
            HealStep(
                capability="retrigger_ingestion",
                description="Re-ingest tenant's repo via Forgewing API",
                verify_after_cycles=3,  # ingestion takes ~90s
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="validate_repo_indexing",
                description="Verify RepoFile nodes now exist in Neptune",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),

    "tenant_no_prs_after_tasks": HealChain(
        pattern_name="tenant_no_prs_after_tasks",
        steps=[
            HealStep(
                capability="validate_tenant_onboarding",
                description="Run full onboarding checklist to find the blocker",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="check_pipeline_health",
                description="Analyze task/PR pipeline for specific blockers",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),

    "tenant_capability_blocked": HealChain(
        pattern_name="tenant_capability_blocked",
        steps=[
            HealStep(
                capability="validate_tenant_onboarding",
                description="Identify which critical capability is failing",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="refresh_tenant_token",
                description="Refresh token in case that's the blocker",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),
    # ----- Performance drift chains (Level 3) -----
    "daemon_cycle_drift": HealChain(
        pattern_name="daemon_cycle_drift",
        steps=[
            HealStep(
                capability="diagnose_daemon_timeout",
                description="Identify which hook is causing cycle time drift",
                verify_after_cycles=3,
            ),
            HealStep(
                capability="check_daemon_code_version",
                description="Check if old code is causing slowdown",
                verify_after_cycles=1,
            ),
        ],
    ),

    "pr_generation_slowdown": HealChain(
        pattern_name="pr_generation_slowdown",
        steps=[
            HealStep(
                capability="check_pipeline_health",
                description="Analyze task pipeline for bottlenecks",
                verify_after_cycles=2,
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="validate_tenant_onboarding",
                description="Full checklist to find the constraint",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),

    "tenant_velocity_drop": HealChain(
        pattern_name="tenant_velocity_drop",
        steps=[
            HealStep(
                capability="validate_tenant_onboarding",
                description="Check if pipeline is blocked",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
            HealStep(
                capability="check_pipeline_health",
                description="Identify specific task-level blockers",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),

    "context_health_decline": HealChain(
        pattern_name="context_health_decline",
        steps=[
            HealStep(
                capability="validate_tenant_onboarding",
                description="Diagnose which intelligence sources are missing",
                verify_after_cycles=1,
                kwargs_builder=_tenant_id,
            ),
        ],
    ),
}


def get_chain(pattern_name: str) -> HealChain | None:
    """Return the heal chain for a pattern, or None if no chain defined."""
    return CHAINS.get(pattern_name)

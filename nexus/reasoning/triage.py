"""
Triage — the decision engine.

Takes a health report or event and returns a TriageDecision with:
    action          — symbolic identifier for the capability to invoke
    confidence      — 0.0-1.0, how sure we are this is the right move
    reasoning       — human-readable explanation
    blast_radius    — safe / moderate / dangerous
    auto_approved   — should `execute` fire without human approval

Known patterns are seeded from real onboarding failures — these are
the first behaviors NEXUS is allowed to act on autonomously.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nexus.config import (
    AUTO_HEAL_CONFIDENCE_THRESHOLD,
    BLAST_DANGEROUS,
    BLAST_MODERATE,
    BLAST_SAFE,
)


@dataclass
class TriageDecision:
    action: str
    confidence: float
    reasoning: str
    blast_radius: str
    auto_approved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "blast_radius": self.blast_radius,
            "auto_approved": self.auto_approved,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Known failure patterns — seeded from Ben's onboarding incidents (2026-04).
#
# Each pattern carries a `match` lambda that takes an event dict (with at
# least `error` or `type` keys), plus the diagnosis and resolution that
# the operator should see when this pattern fires.
# ---------------------------------------------------------------------------
def _err(event: dict[str, Any]) -> str:
    return str(event.get("error") or "").lower()


KNOWN_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "github_permission_denied",
        "match": lambda e: (
            "permission" in _err(e) and "denied" in _err(e)
        )
        or "403" in _err(e),
        "action": "escalate_to_operator",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.95,
        "reasoning": (
            "Tenant's GitHub App installation doesn't have access to their "
            "repo. Installation ID may be wrong."
        ),
        "diagnosis": (
            "GitHub returned 403/permission denied on a write — only the "
            "customer can grant this access."
        ),
        "resolution": (
            "Customer needs to install the GitHub App on their account: "
            "https://github.com/apps/vaultscaler-pr-gateway/installations/new"
        ),
    },
    {
        "name": "bedrock_json_parse",
        "match": lambda e: (
            "cannot parse" in _err(e)
            and ("bedrock" in _err(e) or "json" in _err(e))
        ),
        "action": "retry_with_fence_stripping",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.9,
        "reasoning": (
            "Bedrock returned a response with markdown fences or prose "
            "wrapping the JSON; the quality gate handles this gracefully."
        ),
        "diagnosis": (
            "Non-blocking parse failure on a Bedrock response."
        ),
        "resolution": (
            "Quality gate (quality_gate.py) defaults to approved on parse "
            "failure — no operator action needed."
        ),
    },
    {
        "name": "step_functions_access_denied",
        "match": lambda e: (
            "AccessDeniedException" in str(event_or_empty(e))
            and "states:" in str(event_or_empty(e))
        ),
        "action": "escalate_to_operator",
        "blast_radius": BLAST_DANGEROUS,
        "confidence": 0.85,
        "reasoning": (
            "IAM role missing Step Functions permissions for the "
            "Deliberation Engine."
        ),
        "diagnosis": (
            "AWS Step Functions denied a states:* call from the ECS task role."
        ),
        "resolution": (
            "Add states:DescribeExecution (and states:StartExecution if "
            "needed) to aria-ecs-task-role."
        ),
    },
    {
        "name": "daemon_stale",
        "match": lambda e: (
            e.get("type") == "daemon_stale"
            or "daemon stale" in _err(e)
            or "cycle stale" in _err(e)
            or "no recent cycle" in _err(e)
        ),
        "action": "restart_daemon_service",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.9,
        "reasoning": (
            "Daemon hasn't completed a cycle in >15 minutes; forcing a new "
            "ECS deployment is the standard recovery and is reversible."
        ),
        "diagnosis": "aria-daemon hasn't ticked the graph in the allowed window.",
        "resolution": "Force new ECS deployment of aria-daemon.",
    },
    {
        "name": "ci_failing",
        "match": lambda e: (
            "ci failing" in _err(e)
            or "workflow failed" in _err(e)
            or "green rate" in _err(e)
        ),
        "action": "retrigger_ci",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.85,
        "reasoning": (
            "CI failures detected — attempting to retrigger the most recent "
            "failed workflow. If the retrigger also fails, will escalate."
        ),
        "diagnosis": "GitHub Actions workflows are failing.",
        "resolution": "Auto-retrigger the most recent failed run, then escalate if still failing.",
    },
    # ----- Patterns learned from Ben's onboarding (2026-04-09) -----
    {
        "name": "tenant_no_prs_after_tasks",
        "match": lambda e: (
            e.get("type") == "tenant_health"
            and e.get("task_count", 0) > 0
            and e.get("pr_count", 0) == 0
            and e.get("hours_since_first_task", 0) > 2
        ),
        "action": "validate_tenant_onboarding",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.9,
        "reasoning": (
            "Tenant has pending tasks but no PRs created. Pipeline is blocked "
            "somewhere: token, write access, file indexing, or Bedrock parsing."
        ),
        "diagnosis": "Tasks exist but no PRs — pipeline is stuck.",
        "resolution": "Run validate_tenant_onboarding to identify the specific blocker.",
    },
    {
        "name": "missing_repo_files",
        "match": lambda e: (
            e.get("type") == "tenant_health"
            and e.get("repo_file_count", -1) == 0
            and e.get("ingestion_complete", False)
        ),
        "action": "retrigger_ingestion",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.9,
        "reasoning": (
            "Ingestion completed but no RepoFile nodes in Neptune. "
            "Files were not persisted. Auto-retriggering ingestion."
        ),
        "diagnosis": "Zero RepoFile nodes after ingestion.",
        "resolution": "POST /reingest/{tenant_id} to re-run the ingestion pipeline.",
    },
    {
        "name": "empty_tenant_token",
        "match": lambda e: (
            e.get("type") == "tenant_health"
            and e.get("token_empty", False) is True
        ),
        "action": "refresh_tenant_token",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.95,
        "reasoning": (
            "Tenant's GitHub token is empty. Auto-refreshing from "
            "installation_id via the GitHub App."
        ),
        "diagnosis": "Empty github_token in Secrets Manager.",
        "resolution": "Mint a fresh installation token from the GitHub App.",
    },
    {
        "name": "write_access_denied",
        "match": lambda e: (
            e.get("type") == "tenant_health"
            and e.get("write_access") is False
        ),
        "action": "escalate_to_operator",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.95,
        "reasoning": (
            "GitHub App can read but not write to tenant's repo. "
            "Customer needs to accept updated permissions."
        ),
        "diagnosis": "Write access denied on tenant's repo.",
        "resolution": (
            "Customer must visit https://github.com/settings/installations/"
            "and accept the GitHub App permissions."
        ),
    },
    # ----- Capability Validator patterns (2026-04-10) -----
    {
        "name": "tenant_capability_blocked",
        "match": lambda e: (
            e.get("type") == "capability_report"
            and e.get("overall") == "blocked"
        ),
        "action": "validate_tenant_onboarding",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.9,
        "reasoning": (
            "Capability validator found critical checks failing — tenant "
            "cannot generate PRs. Running full onboarding validation."
        ),
        "diagnosis": "One or more critical capability checks failed.",
        "resolution": "Auto-heal mapped capabilities, then escalate remaining.",
    },
    {
        "name": "tenant_capability_degraded",
        "match": lambda e: (
            e.get("type") == "capability_report"
            and e.get("overall") == "degraded"
        ),
        "action": "check_pipeline_health",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.8,
        "reasoning": (
            "Capability validator found important checks failing — tenant "
            "experience is degraded. Investigating pipeline health."
        ),
        "diagnosis": "Multiple non-critical capability checks failing.",
        "resolution": "Schedule for next cycle, escalate if persistent.",
    },
    {
        "name": "daemon_timeout_recurring",
        "match": lambda e: (
            e.get("type") == "daemon_health"
            and e.get("timeout_count_1h", 0) >= 3
        ),
        "action": "restart_daemon_service",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.85,
        "reasoning": (
            "Daemon has timed out 3+ times in the last hour. A hook is "
            "likely hanging. Auto-restarting."
        ),
        "diagnosis": "Recurring daemon timeouts — likely a hanging hook.",
        "resolution": "Force new ECS deployment + run diagnose_daemon_timeout.",
    },
    # ----- Performance drift patterns (Level 3) -----
    {
        "name": "daemon_cycle_drift",
        "match": lambda e: (
            e.get("type") == "performance_alert"
            and e.get("metric") == "daemon_cycle_duration"
            and e.get("anomalous") is True
        ),
        "action": "diagnose_daemon_timeout",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.85,
        "reasoning": (
            "Daemon cycle duration trending above baseline — proactive "
            "investigation before it becomes a stall."
        ),
        "diagnosis": "Daemon cycle duration anomaly detected.",
        "resolution": "Run diagnose_daemon_timeout to identify the slow hook.",
    },
    {
        "name": "pr_generation_slowdown",
        "match": lambda e: (
            e.get("type") == "performance_alert"
            and e.get("metric") == "pr_generation_time"
            and e.get("trend") == "degrading"
        ),
        "action": "investigate_stuck_tasks",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.75,
        "reasoning": (
            "PR generation time is trending slower for this tenant. "
            "Investigating task pipeline for bottlenecks."
        ),
        "diagnosis": "PR generation time degrading.",
        "resolution": "Check task executor, Bedrock latency, and quality gate.",
    },
    {
        "name": "tenant_velocity_drop",
        "match": lambda e: (
            e.get("type") == "performance_alert"
            and e.get("metric") == "task_velocity"
            and e.get("tasks_per_day", 1) == 0
            and e.get("was_active", False) is True
        ),
        "action": "validate_tenant_onboarding",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.8,
        "reasoning": (
            "Tenant was completing tasks but has dropped to zero — "
            "pipeline may be stalled or customer disengaged."
        ),
        "diagnosis": "Task velocity dropped to zero for a previously active tenant.",
        "resolution": "Validate onboarding + check pipeline health. Alert operator if customer appears disengaged.",
    },
    {
        "name": "context_health_decline",
        "match": lambda e: (
            e.get("type") == "performance_alert"
            and e.get("metric") == "context_health"
            and e.get("active", 8) < 4
        ),
        "action": "validate_tenant_onboarding",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.7,
        "reasoning": (
            "Accretion Core has fewer than 4 active sources — intelligence "
            "quality is degraded. Running onboarding validation to diagnose."
        ),
        "diagnosis": "Accretion context health below threshold.",
        "resolution": "Check which intelligence sources are missing and why.",
    },
]


def event_or_empty(e: dict[str, Any]) -> str:
    """Helper used by lambdas: full event payload as a single searchable string."""
    return " ".join(str(v) for v in e.values())


def _match_pattern(event: str | dict[str, Any]) -> dict[str, Any] | None:
    """Find the first pattern whose match() returns True for this event."""
    if isinstance(event, str):
        event_dict: dict[str, Any] = {"error": event}
    else:
        event_dict = dict(event)
    for pattern in KNOWN_PATTERNS:
        try:
            if pattern["match"](event_dict):
                return pattern
        except Exception:
            continue
    return None


def _decision_from_pattern(pattern: dict[str, Any]) -> TriageDecision:
    return TriageDecision(
        action=pattern["action"],
        confidence=pattern["confidence"],
        reasoning=pattern["reasoning"],
        blast_radius=pattern["blast_radius"],
        auto_approved=False,
        metadata={
            "pattern_name": pattern["name"],
            "diagnosis": pattern.get("diagnosis"),
            "resolution": pattern.get("resolution"),
        },
    )


def triage_tenant_health(report: dict[str, Any]) -> TriageDecision:
    """Decide what to do about a TenantHealthReport."""
    status = report.get("overall_status", "unknown")
    tenant_id = report.get("tenant_id", "unknown")

    if status == "healthy":
        decision = TriageDecision(
            action="noop",
            confidence=1.0,
            reasoning=f"Tenant {tenant_id} is healthy — no action.",
            blast_radius=BLAST_SAFE,
            auto_approved=True,
        )
    elif status == "degraded":
        stuck = report.get("pipeline", {}).get("stuck_task_count", 0)
        if stuck > 0:
            decision = TriageDecision(
                action="investigate_stuck_tasks",
                confidence=0.7,
                reasoning=f"{stuck} task(s) stuck for tenant {tenant_id}; investigate before healing.",
                blast_radius=BLAST_SAFE,
            )
        else:
            decision = TriageDecision(
                action="monitor",
                confidence=0.8,
                reasoning=f"Tenant {tenant_id} degraded but no clear root cause yet.",
                blast_radius=BLAST_SAFE,
            )
    elif status == "critical":
        deployment = report.get("deployment", {})
        if not deployment.get("healthy"):
            decision = TriageDecision(
                action="restart_tenant_service",
                confidence=0.82,
                reasoning=f"Tenant {tenant_id} deployment unhealthy — force new ECS deployment.",
                blast_radius=BLAST_MODERATE,
            )
        else:
            decision = TriageDecision(
                action="escalate_to_operator",
                confidence=0.9,
                reasoning=f"Tenant {tenant_id} critical but deployment healthy — needs human eyes.",
                blast_radius=BLAST_DANGEROUS,
            )
    else:
        decision = TriageDecision(
            action="escalate_to_operator",
            confidence=0.5,
            reasoning=f"Unknown overall_status={status!r} for {tenant_id}.",
            blast_radius=BLAST_MODERATE,
        )

    decision.auto_approved = should_auto_heal(decision)
    _record_triage(f"tenant:{tenant_id}", decision,
                   severity="critical" if status == "critical" else "info")
    return decision


def triage_daemon_health(report: dict[str, Any]) -> TriageDecision:
    if report.get("healthy"):
        decision = TriageDecision(
            action="noop",
            confidence=1.0,
            reasoning="Daemon healthy.",
            blast_radius=BLAST_SAFE,
            auto_approved=True,
        )
    elif report.get("stale"):
        decision = _decision_from_pattern(
            next(p for p in KNOWN_PATTERNS if p["name"] == "daemon_stale")
        )
    elif not report.get("running"):
        decision = TriageDecision(
            action="restart_daemon_service",
            confidence=0.9,
            reasoning="Daemon task not running — start new deployment.",
            blast_radius=BLAST_SAFE,
        )
    else:
        decision = TriageDecision(
            action="escalate_to_operator",
            confidence=0.6,
            reasoning="Daemon unhealthy but unclear pattern — escalate.",
            blast_radius=BLAST_MODERATE,
        )

    decision.auto_approved = should_auto_heal(decision)
    _record_triage("daemon", decision,
                   severity="warning" if not report.get("healthy") else "info")
    return decision


def triage_ci_health(report: dict[str, Any]) -> TriageDecision:
    if report.get("healthy"):
        decision = TriageDecision(
            action="noop",
            confidence=1.0,
            reasoning="CI green.",
            blast_radius=BLAST_SAFE,
            auto_approved=True,
        )
    else:
        pattern = next(p for p in KNOWN_PATTERNS if p["name"] == "ci_failing")
        decision = _decision_from_pattern(pattern)
        decision.metadata["failing_workflows"] = report.get("failing_workflows", [])
        decision.metadata["green_rate_24h"] = report.get("green_rate_24h")

    decision.auto_approved = should_auto_heal(decision)
    _record_triage("ci", decision,
                   severity="warning" if not report.get("healthy") else "info")
    return decision


def triage_capability_report(report: dict[str, Any]) -> TriageDecision:
    """Decide what to do about a CapabilityReport."""
    overall = report.get("overall", "unknown")
    tenant_id = report.get("tenant_id", "unknown")

    if overall == "fully_operational":
        decision = TriageDecision(
            action="noop",
            confidence=1.0,
            reasoning=f"Tenant {tenant_id} fully operational — all capability checks passing.",
            blast_radius=BLAST_SAFE,
            auto_approved=True,
        )
    elif overall == "onboarding":
        decision = TriageDecision(
            action="noop",
            confidence=1.0,
            reasoning=f"Tenant {tenant_id} still onboarding — checks skipped.",
            blast_radius=BLAST_SAFE,
            auto_approved=True,
        )
    elif overall == "blocked":
        event = {"type": "capability_report", "overall": "blocked", "tenant_id": tenant_id}
        event.update(report)
        pattern = _match_pattern(event)
        if pattern:
            decision = _decision_from_pattern(pattern)
        else:
            decision = TriageDecision(
                action="validate_tenant_onboarding",
                confidence=0.9,
                reasoning=f"Tenant {tenant_id} has critical capability failures.",
                blast_radius=BLAST_SAFE,
            )
        decision.metadata["blockers"] = report.get("blockers", [])
    elif overall == "degraded":
        event = {"type": "capability_report", "overall": "degraded", "tenant_id": tenant_id}
        event.update(report)
        pattern = _match_pattern(event)
        if pattern:
            decision = _decision_from_pattern(pattern)
        else:
            decision = TriageDecision(
                action="monitor",
                confidence=0.7,
                reasoning=f"Tenant {tenant_id} degraded — monitoring.",
                blast_radius=BLAST_SAFE,
            )
    else:
        decision = TriageDecision(
            action="monitor",
            confidence=0.5,
            reasoning=f"Unknown capability status '{overall}' for {tenant_id}.",
            blast_radius=BLAST_SAFE,
        )

    decision.auto_approved = should_auto_heal(decision)
    _record_triage(f"capability:{tenant_id}", decision,
                   severity="warning" if overall == "blocked" else "info")
    return decision


def triage_performance_alert(alert: dict[str, Any]) -> TriageDecision:
    """Decide what to do about a performance anomaly."""
    alert["type"] = "performance_alert"  # ensure type is set for pattern matching
    pattern = _match_pattern(alert)
    if pattern:
        decision = _decision_from_pattern(pattern)
        decision.metadata["metric"] = alert.get("metric")
        decision.metadata["value"] = alert.get("value")
        decision.metadata["baseline_mean"] = alert.get("baseline_mean")
    else:
        decision = TriageDecision(
            action="monitor",
            confidence=0.6,
            reasoning=f"Performance drift detected ({alert.get('metric')}) but no pattern matched.",
            blast_radius=BLAST_SAFE,
        )
    decision.auto_approved = should_auto_heal(decision)
    source = f"performance:{alert.get('metric', 'unknown')}"
    if alert.get("tenant_id"):
        source += f":{alert['tenant_id']}"
    _record_triage(source, decision,
                   severity="warning" if pattern else "info")
    return decision


def triage_event(text: str) -> TriageDecision:
    """Best-effort triage of a free-text event message."""
    pattern = _match_pattern(text)
    if pattern:
        decision = _decision_from_pattern(pattern)
    else:
        # Check candidate patterns before escalating — Level 4 self-programming
        from nexus.reasoning.pattern_learner import find_matching_candidate

        candidate = find_matching_candidate("event", "escalate_to_operator")
        if candidate:
            decision = TriageDecision(
                action=candidate.heal_capability,
                confidence=candidate.confidence,
                reasoning=f"Candidate pattern '{candidate.name}' suggests: {candidate.resolution}",
                blast_radius=candidate.blast_radius,
                metadata={
                    "candidate_name": candidate.name,
                    "candidate_match": True,
                    "diagnosis": candidate.diagnosis,
                    "resolution": candidate.resolution,
                },
            )
        else:
            decision = TriageDecision(
                action="escalate_to_operator",
                confidence=0.4,
                reasoning="No known pattern matched — escalating for human review.",
                blast_radius=BLAST_MODERATE,
            )
            _record_unknown_pattern(text)
    decision.auto_approved = should_auto_heal(decision)
    _record_triage("event", decision,
                   severity="warning" if not pattern else "info")
    return decision


def should_auto_heal(decision: TriageDecision) -> bool:
    """
    Auto-heal requires: high confidence, safe blast radius, and a non-noop
    action. Rate-limit enforcement lives in the capability registry so the
    same rule is enforced regardless of caller.
    """
    if decision.action == "noop":
        return True
    return (
        decision.confidence >= AUTO_HEAL_CONFIDENCE_THRESHOLD
        and decision.blast_radius == BLAST_SAFE
    )


# --- Graph recording ---------------------------------------------------------
# Every triage decision is logged to Overwatch's own graph so the platform
# accumulates memory over time. Failures here must never block triage.
import hashlib  # noqa: E402

from nexus import overwatch_graph  # noqa: E402


def _record_triage(source: str, decision: TriageDecision, severity: str = "info") -> None:
    try:
        overwatch_graph.record_event(
            event_type="triage_decision",
            service=source,
            severity=severity,
            details={
                "action": decision.action,
                "confidence": decision.confidence,
                "blast_radius": decision.blast_radius,
                "reasoning": decision.reasoning,
                "pattern_name": (decision.metadata or {}).get("pattern_name"),
            },
        )
        # If a known pattern matched, MERGE+increment its FailurePattern node.
        meta = decision.metadata or {}
        pname = meta.get("pattern_name")
        if pname:
            overwatch_graph.record_failure_pattern(
                name=pname,
                signature=pname,
                diagnosis=meta.get("diagnosis") or decision.reasoning,
                resolution=meta.get("resolution") or decision.action,
                auto_healable=decision.blast_radius == BLAST_SAFE
                and decision.confidence >= AUTO_HEAL_CONFIDENCE_THRESHOLD,
                blast_radius=decision.blast_radius,
                confidence=decision.confidence,
            )
    except Exception:
        pass  # recording must never crash triage


def _record_unknown_pattern(text: str) -> None:
    """An unknown event signature seen for the first time becomes a low-confidence pattern."""
    try:
        digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:10]
        overwatch_graph.record_failure_pattern(
            name=f"unknown_{digest}",
            signature=text[:200],
            diagnosis="Auto-detected unknown failure — needs human review.",
            resolution="Investigate and add a triage pattern if recurring.",
            auto_healable=False,
            blast_radius=BLAST_MODERATE,
            confidence=0.1,
        )
    except Exception:
        pass

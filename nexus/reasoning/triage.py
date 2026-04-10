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
        "action": "escalate_with_diagnosis",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.8,
        "reasoning": (
            "CI failures require a code fix — escalate with the failing "
            "workflow names attached."
        ),
        "diagnosis": "GitHub Actions workflows are failing.",
        "resolution": "Inspect failing workflow logs and push a fix.",
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
    return decision


def triage_event(text: str) -> TriageDecision:
    """Best-effort triage of a free-text event message."""
    pattern = _match_pattern(text)
    if pattern:
        decision = _decision_from_pattern(pattern)
    else:
        decision = TriageDecision(
            action="escalate_to_operator",
            confidence=0.4,
            reasoning="No known pattern matched — escalating for human review.",
            blast_radius=BLAST_MODERATE,
        )
    decision.auto_approved = should_auto_heal(decision)
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

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
# Known failure patterns — seeded from Ben's onboarding incidents.
# ---------------------------------------------------------------------------
KNOWN_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "github_permission_denied",
        "match_any": ["github permission denied", "403", "repository access"],
        "action": "escalate_to_operator",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.95,
        "reasoning": (
            "GitHub permission denied indicates the customer has not "
            "granted NEXUS write access — only they can fix this."
        ),
    },
    {
        "id": "bedrock_parse_failure",
        "match_any": ["cannot parse bedrock", "bedrock response", "json decode"],
        "action": "retry_with_fence_stripping",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.9,
        "reasoning": (
            "Bedrock occasionally wraps JSON in markdown fences; "
            "stripping them and retrying is a safe, idempotent fix."
        ),
    },
    {
        "id": "daemon_stale",
        "match_any": ["daemon stale", "cycle stale", "no recent cycle"],
        "action": "restart_daemon_service",
        "blast_radius": BLAST_SAFE,
        "confidence": 0.85,
        "reasoning": (
            "Daemon hasn't cycled in the allowed window — forcing a new "
            "ECS deployment is the standard recovery and is reversible."
        ),
    },
    {
        "id": "ci_failing",
        "match_any": ["ci failing", "workflow failed", "green rate"],
        "action": "escalate_with_diagnosis",
        "blast_radius": BLAST_MODERATE,
        "confidence": 0.8,
        "reasoning": (
            "CI failures require a code fix — NEXUS should escalate "
            "with the failing workflow names attached."
        ),
    },
]


def _match_pattern(text: str) -> dict[str, Any] | None:
    needle = (text or "").lower()
    for pattern in KNOWN_PATTERNS:
        for key in pattern["match_any"]:
            if key in needle:
                return pattern
    return None


def _decision_from_pattern(pattern: dict[str, Any]) -> TriageDecision:
    return TriageDecision(
        action=pattern["action"],
        confidence=pattern["confidence"],
        reasoning=pattern["reasoning"],
        blast_radius=pattern["blast_radius"],
        auto_approved=False,
        metadata={"pattern_id": pattern["id"]},
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
            next(p for p in KNOWN_PATTERNS if p["id"] == "daemon_stale")
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
        pattern = next(p for p in KNOWN_PATTERNS if p["id"] == "ci_failing")
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

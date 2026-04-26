"""Phase 2 report catalog — 12 reports, 3 feasible, 9 deferred.

Each entry surfaces a structured ``deferred_reason`` so the API and UI
can render gaps explicitly. See ``docs/REPORTS_PHASE_2_INVENTORY.md``
for substrate-fit analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


# Structured reasons. Each maps to a substrate gap noted in the inventory.
DEFER_PHASE_0B = "requires_phase_0b_log_correlation"
DEFER_MECH_2_CLASSIFIER = "requires_mechanism_2_classifier_table"
DEFER_LEARNED_PATTERN = "requires_learned_pattern_library"
DEFER_CLASSIFIER_PROPOSALS = "requires_classifier_proposals_schema"
DEFER_ONTOLOGY_SNAPSHOT = "requires_ontology_snapshot_history"
DEFER_CAPABILITY_GAP = "requires_echo_capability_gap_capture"
DEFER_MUTATION_TOOLING = "requires_mutation_tooling"

ALL_DEFER_REASONS = {
    DEFER_PHASE_0B, DEFER_MECH_2_CLASSIFIER, DEFER_LEARNED_PATTERN,
    DEFER_CLASSIFIER_PROPOSALS, DEFER_ONTOLOGY_SNAPSHOT,
    DEFER_CAPABILITY_GAP, DEFER_MUTATION_TOOLING,
}


@dataclass(frozen=True)
class ReportSpec:
    report_id: str
    name: str
    tier: int                       # 1..4 per the architecture spec
    audience: str
    description: str
    params_schema: dict             # JSON-schema-ish; empty dict = no params
    builder: Optional[Callable] = None  # None => deferred
    deferred_reasons: tuple[str, ...] = field(default_factory=tuple)
    required_tools: tuple[str, ...] = field(default_factory=tuple)

    @property
    def feasible_now(self) -> bool:
        return self.builder is not None and not self.deferred_reasons


def _builders():
    # Lazy import to avoid circulars during module load.
    from nexus.reports.builders import (
        fleet_health, pipeline_activity, tenant_profile,
    )
    return {
        "fleet_health": fleet_health.build,
        "pipeline_activity": pipeline_activity.build,
        "tenant_profile": tenant_profile.build,
    }


def build_catalog() -> dict[str, ReportSpec]:
    b = _builders()
    specs: list[ReportSpec] = [
        ReportSpec(
            report_id="fleet_health",
            name="Fleet Health Overview",
            tier=1,
            audience="Ian, daily, first 30 seconds of the day",
            description=(
                "Tenants by health status (Green/Amber/Red), top-active and "
                "top-troubled. Current state only — 7-day trend deferred "
                "until snapshot history exists."
            ),
            params_schema={},
            builder=b["fleet_health"],
            required_tools=("list_aws_resources", "read_customer_tenant_state"),
        ),
        ReportSpec(
            report_id="critical_findings_24h",
            name="Critical Findings (last 24h)",
            tier=1,
            audience="Ian, ad-hoc — what broke recently?",
            description="Critical-severity events across the fleet, classifier-grouped.",
            params_schema={},
            deferred_reasons=(DEFER_PHASE_0B, DEFER_MECH_2_CLASSIFIER),
        ),
        ReportSpec(
            report_id="pipeline_activity_24h",
            name="Pipeline Activity (last 24h)",
            tier=1,
            audience="Ian, daily — deployment trust thermometer",
            description=(
                "All deployments fleet-wide last 24h, success/fail breakdown. "
                "Groups by raw build status; semantic-failure-type grouping "
                "deferred until classifier substrate exists."
            ),
            params_schema={},
            builder=b["pipeline_activity"],
            required_tools=("list_aws_resources", "read_customer_pipeline"),
        ),
        ReportSpec(
            report_id="tenant_profile",
            name="Tenant Operational Profile",
            tier=2,
            audience="Ian, on-demand for a specific tenant",
            description=(
                "Single-tenant deep dive: ECS/ALB state, recent deploys, "
                "ARIA conversation activity, ontology counts."
            ),
            params_schema={
                "tenant_id": {"type": "string", "required": True,
                              "description": "Full tenant ID, forge-XXXX..."},
            },
            builder=b["tenant_profile"],
            required_tools=(
                "read_customer_tenant_state", "read_customer_pipeline",
                "read_aria_conversations", "read_customer_ontology",
            ),
        ),
        ReportSpec(
            report_id="tenant_failure_diagnose",
            name="Tenant Failure Diagnose",
            tier=2,
            audience="Ian, when a tenant is in red state",
            description="Three-tier investigation (what / why / fix).",
            params_schema={"tenant_id": {"type": "string", "required": True}},
            deferred_reasons=(DEFER_PHASE_0B, DEFER_LEARNED_PATTERN),
        ),
        ReportSpec(
            report_id="conversation_trajectory",
            name="Tenant Conversation Trajectory",
            tier=2,
            audience="Ian, post-incident or for ARIA quality review",
            description="Per-conversation classifier proposals, quality dimensions, trajectory.",
            params_schema={"tenant_id": {"type": "string", "required": True}},
            deferred_reasons=(DEFER_CLASSIFIER_PROPOSALS,),
        ),
        ReportSpec(
            report_id="cross_tenant_failure_patterns",
            name="Cross-Tenant Failure Patterns",
            tier=3,
            audience="Ian, weekly review",
            description="Failure classifications grouped across the fleet, novel/spreading patterns.",
            params_schema={},
            deferred_reasons=(DEFER_PHASE_0B, DEFER_MECH_2_CLASSIFIER),
        ),
        ReportSpec(
            report_id="compounding_loop_health",
            name="Compounding Loop Health",
            tier=3,
            audience="Ian, weekly — strategic",
            description="Ontology accretion, grounding rate, compounding-loop indicators.",
            params_schema={},
            deferred_reasons=(DEFER_ONTOLOGY_SNAPSHOT,),
        ),
        ReportSpec(
            report_id="goal_health",
            name="Goal Health (V1 parity)",
            tier=3,
            audience="Ian, daily — top-of-dashboard scorecard",
            description="Synthesized dashboard-top scorecard from Reports 1-3.",
            params_schema={},
            deferred_reasons=(DEFER_MECH_2_CLASSIFIER,),
        ),
        ReportSpec(
            report_id="recommended_actions",
            name="Recommended Actions Queue",
            tier=4,
            audience="Ian, daily",
            description="Echo-recommended actions with approve/modify/reject.",
            params_schema={},
            deferred_reasons=(DEFER_LEARNED_PATTERN, DEFER_MUTATION_TOOLING),
        ),
        ReportSpec(
            report_id="pattern_based_action_plans",
            name="Pattern-Based Action Plans",
            tier=4,
            audience="Ian, weekly",
            description="Cross-tenant pattern → batch fix plans.",
            params_schema={},
            deferred_reasons=(DEFER_PHASE_0B, DEFER_LEARNED_PATTERN, DEFER_MUTATION_TOOLING),
        ),
        ReportSpec(
            report_id="capability_gap",
            name="Capability Gap & Investment Suggestions",
            tier=4,
            audience="Ian, monthly",
            description="What Echo couldn't answer; suggested next capabilities.",
            params_schema={},
            deferred_reasons=(DEFER_CAPABILITY_GAP,),
        ),
    ]
    return {s.report_id: s for s in specs}

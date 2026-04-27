"""OperatorFeature: an observable capability of the operational substrate.

Lives in canonical Layer 3 (the operational graph, owned by Overwatch's
``nexus/overwatch_graph.py``). Distinct from
``nexus/ontology/schema.py:Feature`` (the founder-ontology Layer 2
concept used by mechanism3). The Python class is in a separate module
and the Neptune label is ``OperatorFeature`` (not ``Feature``), so the
two coexist without collision.

Edges (registered as constants in ``nexus/overwatch_graph.py``):
- ``OPERATOR_DEPENDS_ON`` → operational nodes (ECSService, Lambda,
  LogGroup, etc.)
- ``OPERATOR_COMPOSES`` → other OperatorFeatures (composition)
- ``OPERATOR_REFERENCES`` → Layer 2 founder-ontology nodes
  (Feature / Decision / Hypothesis)
- ``OPERATOR_EVIDENCED_BY`` → Layer 1/3 AnalysisReport, TrajectoryInsight

Dependencies are NOT stored as a property on the node — they're stored
as edges and walked at read time via
``nexus.operator_features.persistence.walk_dependencies(feature_id)``.

Refs:
- /tmp/phase_0e_design_20260427_0653.md
- docs/OPERATIONAL_TRUTH_SUBSTRATE.md (canonical layer numbering)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .evidence import EvidenceQuery, FeatureTier
from .signals import HealthSignal


class OperatorFeature(BaseModel):
    """An observable capability of the operational substrate."""
    model_config = ConfigDict(frozen=True)

    feature_id: str  # snake_case slug: "ontology", "mission", "proposal_cards"
    name: str  # display name: "Ontology Capture"
    tier: FeatureTier
    description: str  # one-paragraph summary of what this Feature does

    health_signals: list[HealthSignal]
    evidence_queries: list[EvidenceQuery]
    falsifiability: str  # "RED when X; GREEN when Y; AMBER otherwise"

    # Optional extended signals (Tier 2/3 may use these; Tier 1 may not).
    extended_signals: dict[str, Any] | None = None

    # Provenance.
    owner: str | None = None  # team/person maintaining the report definition
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    version_id: int = 1  # canonical: every mutation creates a new version

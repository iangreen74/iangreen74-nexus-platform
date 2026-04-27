"""Phase 0e fractal observability framework — OperatorFeature schema.

OperatorFeature is the operational observability concept that the
fractal observability framework reports against. It lives in canonical
Layer 3 (the operational graph, owned by Overwatch's
``nexus/overwatch_graph.py``).

Distinct from ``nexus/ontology/schema.py:Feature`` (the founder-ontology
Layer 2 concept used by mechanism3). The two coexist by living in
separate Python modules and separate Neptune label namespaces; see
``docs/observability.md`` (0e.6) for layer separation details.

The existing ``nexus/capabilities/feature_health.py`` framework is NOT
modified by Phase 0e — it continues running. OperatorFeature reaches
parity in subsequent sub-prompts (engine, Echo tool, ontology instance,
UI, docs); deprecation of the older framework follows.

Refs:
- /tmp/phase_0e_design_20260427_0653.md
- docs/OPERATIONAL_TRUTH_SUBSTRATE.md (canonical layer numbering)
"""
from .evidence import EvidenceQuery, EvidenceQueryKind, FeatureTier
from .schema import OperatorFeature
from .signals import HealthSignal, SignalQueryKind, SignalStatus

__all__ = [
    "OperatorFeature",
    "EvidenceQuery", "EvidenceQueryKind", "FeatureTier",
    "HealthSignal", "SignalStatus", "SignalQueryKind",
]

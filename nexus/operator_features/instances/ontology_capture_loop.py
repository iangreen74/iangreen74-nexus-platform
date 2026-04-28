"""OperatorFeature instance: the ontology capture loop.

First canonical OperatorFeature. Encodes Sprint 15 Day 3-4's closure of
Bug 4 — the loop turning founder conversation turns into typed ontology
nodes (Feature / Decision / Hypothesis) with all required fields
populated by the Haiku classifier, not derived defaults.

Four health signals ship (loop-alive count, source_turn_id linkage,
pending-stale rate, extraction quality) — all POSTGRES_QUERY, all
implemented in the signal evaluator. Two Day-3/4 concerns defer pending
new evidence kinds: schema round-trip integrity (file/git read) and
Lambda staleness (AWS+git correlation). Four EvidenceQueries split
between POSTGRES_QUERY and NEPTUNE_CYPHER for cross-store visibility.

Refs: docs/MECHANISM1.md, migration 016.
"""
from __future__ import annotations

from nexus.operator_features.evidence import (
    EvidenceQuery, EvidenceQueryKind, FeatureTier,
)
from nexus.operator_features.schema import OperatorFeature
from nexus.operator_features.signals import HealthSignal, SignalQueryKind

FEATURE_ID = "ontology_capture_loop"

_W24H = "NOW() - INTERVAL '24 hours'"

# "Row has all classifier-required fields populated for its object_type" —
# locks the per-type contract in one SQL fragment reused across signals.
#
# Mirrors `nexus.ontology.schema.{Feature,Decision,Hypothesis}.REQUIRED_TYPE_FIELDS`
# exactly, with the classifier_proposals → ontology field-name mapping
# from `nexus.mechanism1.proposals.dispose`:
#   Feature.name        → classifier_proposals.title
#   Feature.description → classifier_proposals.summary
#   Decision.name       → classifier_proposals.title
#   (other names match column names directly)
#
# Drift between this fragment and the canonical REQUIRED_TYPE_FIELDS
# would silently false-pass loop-alive and extraction-quality signals.
# Sprint 16 follow-up: schema-round-trip-integrity signal guards this.
_FULL = (
    "((object_type='decision' AND title IS NOT NULL AND context IS NOT NULL "
    "  AND choice_made IS NOT NULL AND reasoning IS NOT NULL "
    "  AND decided_at IS NOT NULL AND decided_by IS NOT NULL) "
    " OR (object_type='hypothesis' AND statement IS NOT NULL "
    "     AND why_believed IS NOT NULL AND how_will_be_tested IS NOT NULL) "
    " OR (object_type='feature' AND title IS NOT NULL "
    "     AND summary IS NOT NULL AND project_id IS NOT NULL))"
)

_HS_LOOP_ALIVE = HealthSignal(
    name="capture_loop_accepted_24h",
    description=(
        "Count of 24h accepted Decision/Hypothesis rows with all "
        "type-required fields populated. Postgres-side Bug-4-closure "
        "proxy: when 0, the loop is broken between Lambda and writer."
    ),
    query_kind=SignalQueryKind.POSTGRES_QUERY,
    query_spec={"target": "v1", "query": (
        "SELECT count(*) FROM classifier_proposals "
        f"WHERE created_at > {_W24H} AND status='accepted' "
        f"AND object_type IN ('decision','hypothesis') AND {_FULL}"
    )},
    unit="count",
    green_threshold=1.0, amber_threshold=1.0, comparison="gte",
)

_HS_TURN_ID_LINKAGE = HealthSignal(
    name="source_turn_id_linkage_pct_24h",
    description=(
        "% of classifier_proposals rows in 24h with non-null "
        "source_turn_id. Bug 4 smoke 2026-04-28 surfaced 100% NULL."
    ),
    query_kind=SignalQueryKind.POSTGRES_QUERY,
    query_spec={"target": "v1", "query": (
        "SELECT COALESCE(ROUND(100.0 * count(source_turn_id)::numeric "
        "/ NULLIF(count(*), 0), 1), 0) FROM classifier_proposals "
        f"WHERE created_at > {_W24H}"
    )},
    unit="percent",
    green_threshold=95.0, amber_threshold=50.0, comparison="gte",
)

_HS_PENDING_STALE = HealthSignal(
    name="pending_stale_rate_pct_24h",
    description=(
        "% of 24h proposals still pending >1h after creation. High = "
        "UI stall, writer 4xx, or hung Accept. CrossServiceWriteAtomicity "
        "orphan inflates this slightly — expected, low baseline."
    ),
    query_kind=SignalQueryKind.POSTGRES_QUERY,
    query_spec={"target": "v1", "query": (
        "SELECT COALESCE(ROUND(100.0 * count(*) FILTER ("
        " WHERE status='pending' "
        " AND created_at < NOW() - INTERVAL '1 hour')::numeric "
        "/ NULLIF(count(*), 0), 1), 0) FROM classifier_proposals "
        f"WHERE created_at > {_W24H}"
    )},
    unit="percent",
    green_threshold=10.0, amber_threshold=50.0, comparison="lte",
)

_HS_EXTRACTION = HealthSignal(
    name="extraction_quality_pct_24h",
    description=(
        "% of 24h proposals where every type-required field is "
        "populated. Catches silent classifier degradation (Bedrock "
        "regression, prompt-token overflow, Lambda staleness). < 60% "
        "means Decision Accept will 400 at the ontology service."
    ),
    query_kind=SignalQueryKind.POSTGRES_QUERY,
    query_spec={"target": "v1", "query": (
        f"SELECT COALESCE(ROUND(100.0 * count(*) FILTER (WHERE {_FULL})"
        "::numeric / NULLIF(count(*), 0), 1), 0) "
        f"FROM classifier_proposals WHERE created_at > {_W24H}"
    )},
    unit="percent",
    green_threshold=90.0, amber_threshold=60.0, comparison="gte",
)

_EQ_PG_COUNTS = EvidenceQuery(
    name="Postgres proposal counts by type and status (24h)",
    kind=EvidenceQueryKind.POSTGRES_QUERY,
    spec={"target": "v1", "query": (
        "SELECT object_type, status, count(*)::int AS n "
        f"FROM classifier_proposals WHERE created_at > {_W24H} "
        "GROUP BY object_type, status ORDER BY object_type, status"
    )},
    section_kind="table", max_results=20,
)

_EQ_NEPTUNE_COUNTS = EvidenceQuery(
    name="Neptune ontology node counts (cumulative, by type)",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (n) WHERE labels(n)[0] IN ['Decision','Hypothesis','Feature'] "
        "RETURN labels(n)[0] AS object_type, count(n) AS count "
        "ORDER BY count DESC"
    )},
    section_kind="table", max_results=10,
)

_EQ_RECENT_PROPOSALS = EvidenceQuery(
    name="Recent classifier proposals (4h, all tenants)",
    kind=EvidenceQueryKind.POSTGRES_QUERY,
    spec={"target": "v1", "query": (
        "SELECT candidate_id::text, tenant_id, object_type, status, "
        f"created_at::text, {_FULL} AS has_required_fields "
        "FROM classifier_proposals "
        "WHERE created_at > NOW() - INTERVAL '4 hours' "
        "ORDER BY created_at DESC LIMIT 100"
    )},
    section_kind="table", max_results=100,
)

_EQ_NEPTUNE_RECENT = EvidenceQuery(
    name="Neptune Decisions and Hypotheses with required fields (recent)",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (n) WHERE labels(n)[0] IN ['Decision','Hypothesis'] "
        "RETURN labels(n)[0] AS object_type, n.id AS id, "
        "n.tenant_id AS tenant_id, n.created_at AS created_at, "
        "(n.choice_made IS NOT NULL OR n.statement IS NOT NULL) "
        "AS has_classifier_fields "
        "ORDER BY n.created_at DESC LIMIT 25"
    )},
    section_kind="table", max_results=25,
)

_FALSIFIABILITY = (
    "GREEN when, in the trailing 24h window: ≥1 accepted Decision or "
    "Hypothesis exists in classifier_proposals with every type-required "
    "field populated by the classifier (not derived defaults); "
    "source_turn_id linkage ≥95%; pending-stale rate ≤10%; extraction "
    "quality ≥90%. AMBER when any threshold trips its amber band. RED "
    "when no fully-populated Decision or Hypothesis has been accepted "
    "in 24h, OR extraction <60%, OR linkage <50%, OR pending-stale >50%."
)

_DESCRIPTION = (
    "Turns founder conversation turns into typed Layer-2 ontology nodes "
    "(Feature, Decision, Hypothesis) with type-required fields populated "
    "by the Haiku classifier. Runs across ontology-conversation-classifier "
    "Lambda → classifier_proposals (V1 Postgres) → aria-platform writer "
    "→ nexus propose_object → Neptune. Closed end-to-end Sprint 15 Day 3-4."
)

FEATURE = OperatorFeature(
    feature_id=FEATURE_ID, name="Ontology Capture Loop",
    tier=FeatureTier.CRITICAL, description=_DESCRIPTION,
    health_signals=[_HS_LOOP_ALIVE, _HS_TURN_ID_LINKAGE,
                    _HS_PENDING_STALE, _HS_EXTRACTION],
    evidence_queries=[_EQ_PG_COUNTS, _EQ_NEPTUNE_COUNTS,
                      _EQ_RECENT_PROPOSALS, _EQ_NEPTUNE_RECENT],
    falsifiability=_FALSIFIABILITY, owner="overwatch",
)

__all__ = ["FEATURE", "FEATURE_ID"]

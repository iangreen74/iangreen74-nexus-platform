"""OperatorFeature instance: the customer deployment capability.

Second canonical OperatorFeature. Encodes Sprint 15 Day 5's substrate
read (D bundle 2026-04-28) of the loop turning a customer tenant's
source repo into a running, healthy production deploy on their AWS
account, recorded as DeploymentAttempt nodes against
DeploymentFingerprint stack patterns.

Three health signals ship (NEPTUNE_COUNT / NEPTUNE_AGGREGATE,
implemented by PR-H1 today): customer-tenant successes per 7d, Tenant
aws_role_arn population rate, count of distinct fingerprint stacks
meeting the CAPABILITY_MATRIX 'proven' standard. Four NEPTUNE_CYPHER
EvidenceQueries surface the underlying detail.

Four signals deferred pending new evidence kinds or evaluator changes:
fingerprint_label_correctness_pct (needs PR #88's framework→language
mapping in the evaluator — encoding it in Cypher without verifying
against PR #88's truth would make the signal a function of guessed
mapping; today: 25%);
deploy_attempt_recording_rate_per_sfn_success (cross-source kind: SFN
list-executions + Cypher count — recording invariant is Sprint 16
candidate); stub_handler_rate_recovery_branches (file/git read kind;
today: 4 of 8 task files stubbed); state_machine_drift_source_vs_deployed
(SFN describe + filesystem diff; today: 21 source / 33 deployed,
intentional Fetch*Output companions per D bundle). Add each when its
evidence kind lands by following this instance's pattern.

Refs: docs/CAPABILITY_MATRIX.md, D bundle 2026-04-28, PR #88, PR-H1.
"""
from __future__ import annotations

from nexus.operator_features.evidence import (
    EvidenceQuery, EvidenceQueryKind, FeatureTier,
)
from nexus.operator_features.schema import OperatorFeature
from nexus.operator_features.signals import HealthSignal, SignalQueryKind

FEATURE_ID = "deployment_capability"

# ISO-8601 lex compare against Cypher's datetime() — finished_at is stored
# as a UTC ISO string ("2026-04-22T00:10:41.035722+00:00" per D bundle
# substrate read). Lexicographic string comparison is correct for ISO 8601
# with consistent timezone offset. Neptune Analytics openCypher exposes
# datetime() and duration() at server side.
_W7D = "datetime() - duration({days: 7})"

_HS_CUSTOMER_SUCCESSES = HealthSignal(
    name="customer_tenant_successes_7d",
    description=(
        "Count of DeploymentAttempt nodes in the trailing 7d window "
        "with outcome='success' and a non-dogfood, non-test tenant_id. "
        "D bundle 2026-04-28 surfaced 0 — the recording invariant is "
        "broken between SFN execution success and DeploymentAttempt "
        "MERGE. Sprint 16 candidate."
    ),
    query_kind=SignalQueryKind.NEPTUNE_COUNT,
    query_spec={"cypher": (
        "MATCH (a:DeploymentAttempt) "
        "WHERE a.outcome = 'success' "
        "  AND NOT a.tenant_id STARTS WITH 'forge-dogfood-' "
        "  AND NOT a.tenant_id STARTS WITH 'forge-test-' "
        "  AND NOT a.tenant_id STARTS WITH 'test-' "
        f"  AND datetime(a.finished_at) > {_W7D} "
        "RETURN count(a) AS customer_successes"
    )},
    unit="count",
    green_threshold=3.0, amber_threshold=1.0, comparison="gte",
)

_HS_AWS_ROLE_ARN = HealthSignal(
    name="tenant_aws_role_arn_pct",
    description=(
        "% of Tenant nodes with aws_role_arn populated (non-null, "
        "non-empty). D bundle showed 75% — onboarding does not always "
        "complete the AWS connect. CAPABILITY_MATRIX Sprint 11 priority."
    ),
    query_kind=SignalQueryKind.NEPTUNE_AGGREGATE,
    query_spec={"cypher": (
        "MATCH (t:Tenant) "
        "WITH count(t) AS total, "
        "     count(CASE WHEN t.aws_role_arn IS NOT NULL "
        "                AND t.aws_role_arn <> '' THEN 1 END) AS populated "
        "RETURN CASE WHEN total = 0 THEN 0.0 "
        "            ELSE 100.0 * populated / total END AS pct"
    )},
    unit="percent",
    green_threshold=95.0, amber_threshold=70.0, comparison="gte",
)

_HS_PROVEN_STACKS = HealthSignal(
    name="proven_stack_count",
    description=(
        "Distinct DeploymentFingerprint stacks meeting CAPABILITY_MATRIX "
        "'proven' (≥3 successful deploys, last_quality_score ≥0.8). "
        "Website-claim substrate. D bundle 2026-04-28: 1 proven "
        "(javascript/express/1-svc/ecs). Target: 4."
    ),
    query_kind=SignalQueryKind.NEPTUNE_COUNT,
    query_spec={"cypher": (
        "MATCH (f:DeploymentFingerprint) "
        "WHERE f.successful_deploys >= 3 "
        "  AND f.last_quality_score >= 0.8 "
        "RETURN count(DISTINCT f.fingerprint) AS proven_stack_count"
    )},
    unit="count",
    green_threshold=4.0, amber_threshold=2.0, comparison="gte",
)

_EQ_FINGERPRINT_TABLE = EvidenceQuery(
    name="DeploymentFingerprint pattern library state",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (f:DeploymentFingerprint) "
        "RETURN f.fingerprint AS fingerprint, "
        "       f.successful_deploys AS successful_deploys, "
        "       f.last_quality_score AS last_quality_score, "
        "       f.last_success_at AS last_success_at "
        "ORDER BY f.successful_deploys DESC LIMIT 25"
    )},
    section_kind="table", max_results=25,
)

_EQ_RECENT_ATTEMPTS = EvidenceQuery(
    name="Recent DeploymentAttempts (all tenants, last 30)",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (a:DeploymentAttempt) "
        "RETURN a.tenant_id AS tenant_id, "
        "       a.fingerprint AS fingerprint, "
        "       a.outcome AS outcome, "
        "       a.finished_at AS finished_at, "
        "       a.quality_score AS quality_score "
        "ORDER BY a.finished_at DESC LIMIT 30"
    )},
    section_kind="table", max_results=30,
)

_EQ_TENANT_ROLE_ARN = EvidenceQuery(
    name="Tenant aws_role_arn population detail",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (t:Tenant) "
        "RETURN t.tenant_id AS tenant_id, "
        "       t.aws_role_arn AS aws_role_arn, "
        "       t.aws_status AS aws_status, "
        "       t.created_at AS created_at "
        "ORDER BY t.created_at DESC LIMIT 50"
    )},
    section_kind="table", max_results=50,
)

_EQ_CUSTOMER_SUCCESS_BY_STACK = EvidenceQuery(
    name="Customer-tenant successes by fingerprint (7d)",
    kind=EvidenceQueryKind.NEPTUNE_CYPHER,
    spec={"cypher": (
        "MATCH (a:DeploymentAttempt) "
        "WHERE a.outcome = 'success' "
        "  AND NOT a.tenant_id STARTS WITH 'forge-dogfood-' "
        "  AND NOT a.tenant_id STARTS WITH 'forge-test-' "
        "  AND NOT a.tenant_id STARTS WITH 'test-' "
        f"  AND datetime(a.finished_at) > {_W7D} "
        "RETURN a.fingerprint AS fingerprint, count(a) AS successes "
        "ORDER BY successes DESC LIMIT 20"
    )},
    section_kind="table", max_results=20,
)

_FALSIFIABILITY = (
    "GREEN when, in the trailing 7-day window: ≥1 non-dogfood "
    "customer-tenant DeploymentAttempt with outcome='success' "
    "(recording invariant intact); aws_role_arn populated on ≥95% "
    "of Tenants; ≥4 DeploymentFingerprint stacks meet CAPABILITY_MATRIX "
    "'proven' (≥3 deploys at quality_score ≥0.8). AMBER when any "
    "threshold trips its amber band. RED when 0 customer-tenant "
    "successes in 7d, OR aws_role_arn <70%, OR ≤1 proven stacks."
)

_DESCRIPTION = (
    "The customer deployment capability — turning a customer tenant's "
    "source repo into a running, healthy production deploy on their "
    "AWS account. Runs across customer onboarding (Tenant.aws_role_arn) "
    "→ Step Functions deploy state machine → DeploymentAttempt MERGE "
    "→ DeploymentFingerprint stack pattern accrual. The website-claim "
    "substrate: each Proven stack is a website line item."
)

FEATURE = OperatorFeature(
    feature_id=FEATURE_ID, name="Deployment Capability",
    tier=FeatureTier.CRITICAL, description=_DESCRIPTION,
    health_signals=[_HS_CUSTOMER_SUCCESSES, _HS_AWS_ROLE_ARN,
                    _HS_PROVEN_STACKS],
    evidence_queries=[_EQ_FINGERPRINT_TABLE, _EQ_RECENT_ATTEMPTS,
                      _EQ_TENANT_ROLE_ARN, _EQ_CUSTOMER_SUCCESS_BY_STACK],
    falsifiability=_FALSIFIABILITY, owner="overwatch",
)

__all__ = ["FEATURE", "FEATURE_ID"]

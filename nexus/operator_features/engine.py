"""Phase 0e.2 report engine — top-level orchestration.

``generate_feature_report(feature_id, tenant_id)`` reads the
OperatorFeature node from the operational graph, runs the dependency
walker, signal evaluator, and evidence executor, and assembles a
FeatureReport with a derived overall status.

Status derivation (`_derive_overall_status`):
  - Any RED → RED
  - Otherwise any AMBER → AMBER
  - Otherwise any UNKNOWN → UNKNOWN
  - Otherwise (all GREEN, or no signals at all) → GREEN if any inputs,
    UNKNOWN if zero inputs (an OperatorFeature with no deps and no
    signals can't usefully report anything green-or-otherwise).

This module is the only place that knows the report assembly order;
each evaluator (dependencies, signals, evidence) owns its own data
fetch and error handling, and the engine just glues them.
"""
from __future__ import annotations

from nexus.operator_features.dependencies import walk_dependencies
from nexus.operator_features.evidence_executor import execute_evidence_queries
from nexus.operator_features.persistence import read_operator_feature
from nexus.operator_features.report import (
    DependencyStatus, FeatureReport, SignalResult,
)
from nexus.operator_features.signal_evaluator import evaluate_health_signals
from nexus.operator_features.signals import SignalStatus

_FLEET_TENANT = "_fleet"


def generate_feature_report(
    feature_id: str,
    tenant_id: str = _FLEET_TENANT,
) -> FeatureReport:
    """Produce a complete FeatureReport for ``feature_id``.

    Returns an UNKNOWN report with a single note if the feature does
    not exist in the operational graph for the given tenant. Never
    raises — the engine's contract with callers (Echo tool 0e.3, UI
    0e.5) is that a report always comes back, even if degenerate.
    """
    feature = read_operator_feature(feature_id, tenant_id=tenant_id)
    if feature is None:
        return FeatureReport(
            feature_id=feature_id,
            feature_name="<not found>",
            tenant_id=tenant_id,
            overall_status=SignalStatus.UNKNOWN,
            falsifiability="",
            notes=[
                f"OperatorFeature feature_id={feature_id!r} not found "
                f"in operational graph for tenant_id={tenant_id!r}"
            ],
        )

    dependencies = walk_dependencies(feature_id, tenant_id=tenant_id)
    health_signals = evaluate_health_signals(feature)
    evidence_queries = execute_evidence_queries(feature, tenant_id=tenant_id)
    overall = _derive_overall_status(dependencies, health_signals)

    return FeatureReport(
        feature_id=feature.feature_id,
        feature_name=feature.name,
        tenant_id=tenant_id,
        overall_status=overall,
        falsifiability=feature.falsifiability,
        dependencies=dependencies,
        health_signals=health_signals,
        evidence_queries=evidence_queries,
    )


def _derive_overall_status(
    dependencies: list[DependencyStatus],
    health_signals: list[SignalResult],
) -> SignalStatus:
    """Worst-of across dependency + signal statuses.

    A feature with zero inputs (no deps and no signals) cannot
    meaningfully be GREEN — it has nothing to be green about — so
    UNKNOWN is the right default.
    """
    statuses = (
        [d.status for d in dependencies]
        + [s.status for s in health_signals]
    )
    if not statuses:
        return SignalStatus.UNKNOWN
    if SignalStatus.RED in statuses:
        return SignalStatus.RED
    if SignalStatus.AMBER in statuses:
        return SignalStatus.AMBER
    if SignalStatus.UNKNOWN in statuses:
        return SignalStatus.UNKNOWN
    return SignalStatus.GREEN


__all__ = ["generate_feature_report"]

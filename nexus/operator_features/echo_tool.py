"""Echo tool: ``read_holograph(feature_id, tenant_id)`` → FeatureReport JSON.

A *Holograph* is a structured projection of an OperatorFeature's
interior dynamics onto its boundary representation: dependency health,
health-signal evaluations, evidence-query results, and an overall
status (green/amber/red/unknown) derived from the feature's
falsifiability statement.

The Holograph is the *boundary representation*; ``FeatureReport`` is
the Pydantic data structure that carries it. Tool dispatch returns
the FeatureReport serialized to dict for Echo's downstream
consumption.

Naming locked Sprint 15 Day 3 — ``Holograph`` is canonical, no
parallel terminology in code or prose. The internal data structure
name ``FeatureReport`` is preserved (it's the model) but the
operator-facing surface uses ``read_holograph``.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus.operator_features.engine import generate_feature_report
from nexus.operator_features.report import FeatureReport

log = logging.getLogger(__name__)


PARAMETER_SCHEMA = {
    "type": "object",
    "properties": {
        "feature_id": {
            "type": "string",
            "description": (
                "Slug id of the OperatorFeature node in the operational "
                "graph (Layer 3 Neptune). e.g. 'ontology', 'classifier_loop'."
            ),
        },
        "tenant_id": {
            "type": "string",
            "description": (
                "Tenant scope. Defaults to '_fleet' (fleet-level features). "
                "Pass a tenant id like 'forge-1dba4143ca24ed1f' for the rare "
                "tenant-specific OperatorFeature instance."
            ),
        },
    },
    "required": ["feature_id"],
}


_TOOL_DESCRIPTION = (
    "Read the Holograph of an OperatorFeature — a structured projection "
    "of the feature's interior dynamics onto its boundary representation. "
    "Returns dependency health (per ECSService/RDSInstance/Lambda/S3 "
    "target), health-signal evaluations against declared CloudWatch / "
    "Postgres / Logs sources, evidence-query results tagged with their "
    "section_kind for downstream rendering, and an overall status "
    "(green/amber/red/unknown) derived from the falsifiability statement. "
    "Use when the operator asks about the operational state of a specific "
    "feature ('Is the ontology working?', 'Why is X slow?', 'Show me the "
    "Holograph for the classifier loop'). The tool returns ground-truth "
    "evidence drawn from real data sources, not summaries. Engine never "
    "raises: missing features yield a stub Holograph with notes; "
    "per-evaluator failures populate `error` on individual results "
    "without breaking the response."
)


def handler(**params: Any) -> dict:
    """Dispatch entry point. Returns FeatureReport-shaped dict.

    Parameter validation has already happened at the registry layer
    (parameter_schema), so by the time this runs we know feature_id is
    a string and tenant_id (if present) is a string.
    """
    feature_id = params["feature_id"]
    tenant_id = params.get("tenant_id") or "_fleet"

    try:
        report: FeatureReport = generate_feature_report(
            feature_id, tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — engine never raises by contract
        log.exception("read_holograph: engine raised unexpectedly")
        return {
            "feature_id": feature_id,
            "feature_name": "<engine error>",
            "tenant_id": tenant_id,
            "overall_status": "unknown",
            "falsifiability": "",
            "dependencies": [],
            "health_signals": [],
            "evidence_queries": [],
            "notes": [
                f"engine raised unexpectedly: {type(exc).__name__}: {exc}"
            ],
        }

    return report.model_dump(mode="json")


def register_tool() -> None:
    """Register read_holograph with the V2 tool registry.

    Called from ``nexus/overwatch_v2/tools/read_tools/_registration.py``
    at chat-backend startup. Tool is read-only — ``requires_approval``
    is False, ``risk_level`` is LOW.
    """
    from nexus.overwatch_v2.tools.registry import RISK_LOW, ToolSpec, register
    register(ToolSpec(
        name="read_holograph",
        description=_TOOL_DESCRIPTION,
        parameter_schema=PARAMETER_SCHEMA,
        handler=handler,
        requires_approval=False,
        risk_level=RISK_LOW,
    ))


__all__ = ["handler", "register_tool", "PARAMETER_SCHEMA"]

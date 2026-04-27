"""Read/write OperatorFeature nodes through ``nexus/overwatch_graph.py``.

Per canonical: all Layer 3 writes go through ``overwatch_graph``
exclusively. This module is a thin adapter from the Pydantic
``OperatorFeature`` model to the graph backend's MERGE pattern.

Tenant scoping defaults to ``_fleet`` (fleet-level features). Tenant-
specific instances are supported via the ``tenant_id`` parameter; in
practice they are rare — most OperatorFeature nodes are fleet-wide.

Property layout on the Neptune node:
- ``id`` — opaque uuid (assigned by ``_create_node``)
- ``feature_id`` — natural-key slug, used to look up by name
- ``tenant_id`` — defaults to ``_fleet``
- ``name``, ``tier``, ``version_id``, ``created_at``, ``updated_at`` —
  flat queryable properties
- ``definition_json`` — full Pydantic dump (HealthSignal /
  EvidenceQuery lists are nested objects, so they cannot be flat
  Neptune properties)
"""
from __future__ import annotations

import json
from typing import Any

from nexus import overwatch_graph

from .evidence import FeatureTier
from .schema import OperatorFeature

_OPERATOR_FEATURE_LABEL = "OperatorFeature"
_FLEET_TENANT = "_fleet"


def _to_props(feature: OperatorFeature, tenant_id: str) -> dict[str, Any]:
    """Flatten an OperatorFeature into Neptune-compatible node properties."""
    dump = feature.model_dump(mode="json")
    return {
        "feature_id": feature.feature_id,
        "tenant_id": tenant_id,
        "name": feature.name,
        "tier": int(feature.tier),
        "version_id": feature.version_id,
        "created_at": dump["created_at"],
        "updated_at": dump["updated_at"],
        "definition_json": json.dumps(dump),
    }


def _from_node(node: dict[str, Any]) -> OperatorFeature:
    return OperatorFeature.model_validate(json.loads(node["definition_json"]))


def write_operator_feature(
    feature: OperatorFeature,
    tenant_id: str = _FLEET_TENANT,
) -> str:
    """Idempotent MERGE of an OperatorFeature node. Returns its node id.

    In production this delegates to ``overwatch_graph._create_node``,
    which MERGEs by ``id``. Local mode's ``_create_node`` always
    appends, so re-writing the same ``feature_id`` requires removing
    the old row first to keep the local store consistent with the
    production MERGE-by-id behaviour.
    """
    existing = _find_node(feature.feature_id, tenant_id)
    props = _to_props(feature, tenant_id)
    if existing is not None:
        props["id"] = existing["id"]
        if overwatch_graph.MODE != "production":
            with overwatch_graph._lock:
                rows = overwatch_graph._local_store[_OPERATOR_FEATURE_LABEL]
                rows[:] = [r for r in rows if r.get("id") != existing["id"]]
    return overwatch_graph._create_node(_OPERATOR_FEATURE_LABEL, props)


def _find_node(
    feature_id: str,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Internal: find the latest node row for (feature_id, tenant_id)."""
    if overwatch_graph.MODE != "production":
        with overwatch_graph._lock:
            rows = list(overwatch_graph._local_store[_OPERATOR_FEATURE_LABEL])
        candidates = [
            r for r in rows
            if r.get("feature_id") == feature_id
            and r.get("tenant_id") == tenant_id
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r.get("version_id", 0), reverse=True)
        return candidates[0]
    rows = overwatch_graph.query(
        f"MATCH (n:{_OPERATOR_FEATURE_LABEL} "
        "{feature_id: $fid, tenant_id: $tid}) "
        "RETURN n.id AS id, n.definition_json AS definition_json, "
        "n.version_id AS version_id "
        "ORDER BY n.version_id DESC LIMIT 1",
        {"fid": feature_id, "tid": tenant_id},
    )
    return rows[0] if rows else None


def read_operator_feature(
    feature_id: str,
    tenant_id: str = _FLEET_TENANT,
) -> OperatorFeature | None:
    """Read the latest version of an OperatorFeature, or None if missing."""
    node = _find_node(feature_id, tenant_id)
    if node is None:
        return None
    return _from_node(node)


def list_operator_features(
    tier: FeatureTier | None = None,
    tenant_id: str = _FLEET_TENANT,
) -> list[OperatorFeature]:
    """List OperatorFeatures, optionally filtered by tier."""
    if overwatch_graph.MODE != "production":
        with overwatch_graph._lock:
            rows = list(overwatch_graph._local_store[_OPERATOR_FEATURE_LABEL])
        rows = [r for r in rows if r.get("tenant_id") == tenant_id]
    else:
        rows = overwatch_graph.query(
            f"MATCH (n:{_OPERATOR_FEATURE_LABEL} {{tenant_id: $tid}}) "
            "RETURN n.definition_json AS definition_json",
            {"tid": tenant_id},
        )
    features = [_from_node(r) for r in rows if r.get("definition_json")]
    if tier is not None:
        features = [f for f in features if f.tier == tier]
    return features


def add_dependency_edge(
    feature_id: str,
    target_node_id: str,
    target_label: str,
    tenant_id: str = _FLEET_TENANT,
) -> None:
    """Add an OPERATOR_DEPENDS_ON edge to an operational node."""
    node = _find_node(feature_id, tenant_id)
    if node is None:
        raise ValueError(
            f"OperatorFeature {feature_id!r} (tenant={tenant_id!r}) "
            "not found; write it before adding dependencies"
        )
    overwatch_graph._create_edge(
        from_label=_OPERATOR_FEATURE_LABEL,
        from_id=node["id"],
        to_label=target_label,
        to_id=target_node_id,
        edge_type=overwatch_graph.OPERATOR_DEPENDS_ON,
    )


def walk_dependencies(
    feature_id: str,
    depth: int = 1,
    tenant_id: str = _FLEET_TENANT,
) -> list[dict[str, Any]]:
    """Walk OPERATOR_DEPENDS_ON edges. Returns operational node summaries.

    ``depth`` is reserved for future multi-hop walks (0e.2 may need them
    to render compositional dependency trees). The current implementation
    is single-hop; depth>1 is a no-op until 0e.2 needs it.
    """
    node = _find_node(feature_id, tenant_id)
    if node is None:
        return []
    return overwatch_graph._walk_edges(
        from_label=_OPERATOR_FEATURE_LABEL,
        from_id=node["id"],
        edge_type=overwatch_graph.OPERATOR_DEPENDS_ON,
    )

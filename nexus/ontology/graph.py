"""Neptune write layer for Loom v0.

Thin wrapper over nexus.overwatch_graph.query() with ontology-specific
Cypher MERGE and MATCH templates. Same neptune-graph boto3 client as the
rest of nexus — no new connection or credentials.

Writes are MERGE by `id`; SET applies the full props dict. Node labels
match ObjectType values: Feature, Decision, Hypothesis.
"""
from __future__ import annotations

import logging
from typing import Any

from nexus.ontology.exceptions import (
    GraphWriteError,
    ObjectNotFoundError,
    TenantMismatchError,
)
from nexus.ontology.schema import OntologyObject
from nexus.ontology.types import ObjectType
from nexus.overwatch_graph import query

log = logging.getLogger(__name__)

_MERGE = (
    "MERGE (n:{label} {{id: $id}}) "
    "SET n += $props "
    "RETURN n.id AS id, n.version_id AS version_id"
)

_READ = (
    "MATCH (n:{label} {{id: $id}}) "
    "RETURN properties(n) AS props "
    "LIMIT 1"
)


def _assert_label(label: str) -> None:
    if label not in ObjectType.values():
        raise GraphWriteError(f"Refusing to operate on unknown label: {label!r}")


def merge_object(obj: OntologyObject) -> dict[str, Any]:
    """MERGE an ontology object node. Returns {id, version_id}."""
    _assert_label(obj.object_type)
    cypher = _MERGE.format(label=obj.object_type)
    try:
        rows = query(cypher, {"id": obj.id, "props": obj.to_neptune_props()})
    except Exception as e:
        log.exception("Neptune MERGE failed for %s id=%s", obj.object_type, obj.id)
        raise GraphWriteError(f"MERGE failed: {e}") from e
    if not rows:
        return {"id": obj.id, "version_id": obj.version_id}
    return {
        "id": rows[0].get("id", obj.id),
        "version_id": rows[0].get("version_id", obj.version_id),
    }


def read_object(object_type: str, object_id: str, tenant_id: str) -> dict[str, Any]:
    """Read an object's properties with tenant check."""
    _assert_label(object_type)
    cypher = _READ.format(label=object_type)
    try:
        rows = query(cypher, {"id": object_id})
    except Exception as e:
        log.exception("Neptune MATCH failed for %s id=%s", object_type, object_id)
        raise GraphWriteError(f"MATCH failed: {e}") from e
    if not rows:
        raise ObjectNotFoundError(f"No {object_type} with id={object_id}")
    props = rows[0].get("props") or rows[0]
    if not isinstance(props, dict):
        raise GraphWriteError(f"Unexpected response shape: {rows[0]!r}")
    if props.get("tenant_id") != tenant_id:
        raise TenantMismatchError(
            f"{object_type} id={object_id} does not belong to tenant {tenant_id}"
        )
    return props

"""V2 service layer — propose_object, update_object, create_link, plus reads.

Sequential dual-write: Postgres-first (canonical version history) then
Neptune-MERGE (queryable projection). This is NOT transactional; if the
Neptune write fails after Postgres succeeds, reconciliation is the
recovery story (matches existing nexus/ontology/service.py).

In NEXUS_MODE != 'production', writes go to local_store._local_store
instead of real Postgres+Neptune.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from nexus.overwatch_v2.ontology import graph, local_store, postgres
from nexus.overwatch_v2.ontology.exceptions import (
    V2ObjectNotFoundError, V2PostgresNotConfiguredError, V2SchemaValidationError,
)
from nexus.overwatch_v2.ontology.schema import (
    OBJECT_TYPE_REGISTRY, object_class_for, validate_edge,
)
from nexus.overwatch_v2.ontology.types import NodeType, TENANT_ID

log = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.environ.get("NEXUS_MODE", "local").lower() == "production"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filter_fields(cls, props: Mapping[str, Any]) -> dict:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in props.items() if k in allowed}


def propose_object(
    object_type: str,
    properties: Mapping[str, Any],
    actor: str = "reasoner",
    source_conversation_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new V2 object. Returns {object_id, version_id, action_event_id}."""
    if object_type not in NodeType.values():
        raise V2SchemaValidationError(
            f"object_type {object_type!r} not in {sorted(NodeType.values())}"
        )
    cls = object_class_for(object_type)
    now = _now()
    object_id = str(uuid.uuid4())
    seed = dict(properties)
    seed.update(
        id=object_id, tenant_id=TENANT_ID, object_type=object_type,
        version_id=1, created_at=now, valid_from=now, created_by=actor,
    )
    obj = cls(**_filter_fields(cls, seed))
    props_json = obj.to_neptune_props()
    action_event_id = str(uuid.uuid4())

    if _is_production():
        postgres.insert_version(
            object_id=object_id, version_id=1, object_type=object_type,
            properties=props_json, valid_from=now, created_by=actor,
        )
        graph.merge_object(object_type, props_json)
    else:
        local_store.append_object({
            **props_json, "_action_event_id": action_event_id,
            "_source_conversation_id": source_conversation_id,
        })

    log.info("v2 propose_object: type=%s id=%s actor=%s", object_type, object_id, actor)
    return {
        "object_id": object_id, "version_id": 1,
        "action_event_id": action_event_id,
    }


def update_object(
    object_id: str,
    properties: Mapping[str, Any],
    actor: str = "reasoner",
) -> dict[str, Any]:
    """Mutate an existing object. Increments version_id; supersedes prior version."""
    current = get_object(object_id)
    if current is None:
        raise V2ObjectNotFoundError(f"V2 object not found: id={object_id}")
    object_type = current["object_type"]
    new_version = int(current["version_id"]) + 1
    cls = object_class_for(object_type)
    now = _now()

    merged = dict(current.get("properties") or current)
    merged.update(properties)
    merged.update(
        id=object_id, tenant_id=TENANT_ID, object_type=object_type,
        version_id=new_version, created_at=now, valid_from=now,
        valid_to=None, created_by=actor,
    )
    obj = cls(**_filter_fields(cls, merged))
    props_json = obj.to_neptune_props()
    action_event_id = str(uuid.uuid4())

    if _is_production():
        postgres.supersede_prior_version(object_id, valid_to=now)
        postgres.insert_version(
            object_id=object_id, version_id=new_version,
            object_type=object_type, properties=props_json,
            valid_from=now, created_by=actor,
        )
        graph.merge_object(object_type, props_json)
    else:
        local_store.supersede_prior_version(object_id, valid_to=now)
        local_store.append_object({
            **props_json, "_action_event_id": action_event_id,
        })

    log.info("v2 update_object: id=%s v=%s actor=%s", object_id, new_version, actor)
    return {
        "object_id": object_id, "version_id": new_version,
        "action_event_id": action_event_id,
    }


def create_link(
    from_id: str,
    to_id: str,
    edge_type: str,
    properties: Optional[dict] = None,
) -> dict[str, Any]:
    """Create a typed edge in Neptune (or local_store). Append-only."""
    from_obj = get_object(from_id)
    if from_obj is None:
        raise V2ObjectNotFoundError(f"from_id not found: {from_id}")
    to_obj = get_object(to_id)
    to_type = to_obj["object_type"] if to_obj else None
    validate_edge(edge_type, from_obj["object_type"], to_type)
    edge_id = str(uuid.uuid4())
    action_event_id = str(uuid.uuid4())

    if _is_production():
        graph.merge_edge(edge_type, from_id, to_id, edge_id, properties)
    else:
        local_store.append_edge({
            "edge_id": edge_id, "edge_type": edge_type,
            "from_id": from_id, "from_type": from_obj["object_type"],
            "to_id": to_id, "to_type": to_type,
            "properties": properties or {}, "created_at": _now(),
            "_action_event_id": action_event_id,
        })

    log.info("v2 create_link: %s -> %s [%s]", from_id, to_id, edge_type)
    return {"edge_id": edge_id, "action_event_id": action_event_id}


def get_object(object_id: str, version: Optional[int] = None) -> Optional[dict]:
    """Return the current (or specified-version) row for an object, or None."""
    if _is_production():
        try:
            return postgres.fetch_version(object_id, version)
        except V2PostgresNotConfiguredError:
            return None
    return local_store.find_object(object_id, version)


def list_objects_by_type(object_type: str, limit: int = 100) -> list[dict]:
    if object_type not in OBJECT_TYPE_REGISTRY:
        raise V2SchemaValidationError(f"unknown object_type: {object_type}")
    if _is_production():
        return graph.list_by_label(object_type, limit)
    rows = [r for r in local_store.list_by_type(object_type) if r.get("valid_to") is None]
    return rows[:limit]


def query(cypher: str, parameters: Optional[dict] = None) -> list[dict]:
    """Pass-through openCypher. Local mode returns []."""
    if _is_production():
        return graph.query(cypher, parameters or {})
    return []

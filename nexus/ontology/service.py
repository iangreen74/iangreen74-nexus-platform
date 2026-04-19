"""Loom v0 service layer.

Two actions per STARTUP_ONTOLOGY.md Section 10:
    propose_object  — create new object (version_id=1)
    update_object   — modify existing, incrementing version_id

All validation via dataclass __post_init__. All Neptune writes via graph.
Both actions return {object_id, version_id, action_event_id}.

action_event_id is a UUID generated here. S3/Iceberg ActionEvent write
and Postgres version-history row land in subsequent commits as additive
post-commit hooks — the service contract does not change.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import fields
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from nexus.ontology import graph
from nexus.ontology.exceptions import SchemaValidationError
from nexus.ontology.schema import object_class_for
from nexus.ontology.types import ObjectType

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _filter_fields(cls, props: Mapping[str, Any]) -> dict:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in props.items() if k in allowed}


def propose_object(
    object_type: str,
    tenant_id: str,
    properties: Mapping[str, Any],
    actor: str,
    project_id: Optional[str] = None,
    source_conversation_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a new ontology object. Returns {object_id, version_id, action_event_id}."""
    if object_type not in ObjectType.values():
        raise SchemaValidationError(f"object_type {object_type!r} not in {sorted(ObjectType.values())}")
    if not tenant_id:
        raise SchemaValidationError("tenant_id is required")
    if not actor:
        raise SchemaValidationError("actor is required")

    cls = object_class_for(object_type)
    now = _now_iso()
    object_id = str(uuid.uuid4())

    seed = dict(properties)
    seed.update(
        id=object_id, tenant_id=tenant_id, project_id=project_id,
        object_type=object_type, version_id=1,
        created_at=now, updated_at=now, created_by=actor,
    )
    obj = cls(**_filter_fields(cls, seed))

    result = graph.merge_object(obj)
    log.info("Loom propose_object: type=%s tenant=%s id=%s actor=%s",
             object_type, tenant_id, object_id, actor)
    return {
        "object_id": result["id"],
        "version_id": result["version_id"],
        "action_event_id": str(uuid.uuid4()),
    }


def update_object(
    object_type: str,
    object_id: str,
    tenant_id: str,
    updated_properties: Mapping[str, Any],
    actor: str,
    change_reason: str,
) -> dict[str, Any]:
    """Update an existing object. Increments version_id."""
    if not change_reason:
        raise SchemaValidationError("change_reason is required for update_object")

    cls = object_class_for(object_type)
    current = graph.read_object(object_type, object_id, tenant_id)

    merged = dict(current)
    merged.update(updated_properties)
    merged["version_id"] = int(current.get("version_id", 1)) + 1
    merged["updated_at"] = _now_iso()
    merged["id"] = object_id
    merged["tenant_id"] = tenant_id
    merged["object_type"] = object_type

    new_obj = cls(**_filter_fields(cls, merged))

    result = graph.merge_object(new_obj)
    log.info("Loom update_object: type=%s tenant=%s id=%s v=%s actor=%s reason=%s",
             object_type, tenant_id, object_id, result["version_id"], actor, change_reason)
    return {
        "object_id": result["id"],
        "version_id": result["version_id"],
        "action_event_id": str(uuid.uuid4()),
    }

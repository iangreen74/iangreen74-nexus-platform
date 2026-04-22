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
from nexus.ontology.eval_corpus import write_action_event
from nexus.ontology.exceptions import SchemaValidationError
from nexus.ontology.postgres import PostgresNotConfiguredError, write_version
from nexus.ontology.schema import object_class_for
from nexus.ontology.types import ObjectType

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_postgres_write(**kwargs) -> str | None:
    """Write Postgres version row if DATABASE_URL is configured.
    Returns version_id on success, None if Postgres not provisioned."""
    try:
        return write_version(**kwargs)
    except PostgresNotConfiguredError:
        log.debug("Postgres not configured — skipping version write")
        return None


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

    # Postgres first — version history must never be irrecoverably lost.
    pg_version_id = _try_postgres_write(
        ontology_id=object_id, tenant_id=tenant_id, project_id=project_id,
        object_type=object_type, object_data=obj.to_neptune_props(),
        proposed_via=f"propose:{actor}",
    )

    result = graph.merge_object(obj)

    # Layer 3: eval corpus (append-only, never blocks the mutation)
    action_event_id = write_action_event(
        tenant_id=tenant_id, project_id=project_id,
        ontology_id=object_id, version_id=pg_version_id,
        object_type=object_type, mutation_kind="propose",
        caller=actor, proposed_via=f"propose:{actor}",
        old_state=None, new_state=obj.to_neptune_props(),
    ) or str(uuid.uuid4())

    log.info("Loom propose_object: type=%s tenant=%s id=%s actor=%s",
             object_type, tenant_id, object_id, actor)
    return {
        "object_id": result["id"],
        "version_id": result["version_id"],
        "action_event_id": action_event_id,
        "pg_version_id": pg_version_id,
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

    pg_version_id = _try_postgres_write(
        ontology_id=object_id, tenant_id=tenant_id,
        project_id=merged.get("project_id"),
        object_type=object_type, object_data=new_obj.to_neptune_props(),
        proposed_via=f"update:{actor}:{change_reason[:50]}",
    )

    result = graph.merge_object(new_obj)

    action_event_id = write_action_event(
        tenant_id=tenant_id, project_id=merged.get("project_id"),
        ontology_id=object_id, version_id=pg_version_id,
        object_type=object_type, mutation_kind="update",
        caller=actor, proposed_via=f"update:{actor}:{change_reason[:50]}",
        old_state=current, new_state=new_obj.to_neptune_props(),
    ) or str(uuid.uuid4())

    log.info("Loom update_object: type=%s tenant=%s id=%s v=%s actor=%s reason=%s",
             object_type, tenant_id, object_id, result["version_id"], actor, change_reason)
    return {
        "object_id": result["id"],
        "version_id": result["version_id"],
        "action_event_id": action_event_id,
        "pg_version_id": pg_version_id,
    }

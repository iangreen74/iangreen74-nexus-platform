"""Loom v0 API routes — /api/ontology/*.

Thin HTTP-to-service adapter. All business logic lives in
nexus.ontology.service. Matches nexus convention: async def handlers,
dict[str, Any] bodies via Body, HTTPException for errors.

Error-to-HTTP mapping:
    SchemaValidationError  -> 400
    ObjectNotFoundError    -> 404
    TenantMismatchError    -> 403
    GraphWriteError        -> 500
    OntologyError (other)  -> 500

Security (v0): unauthenticated, matching existing console convention.
Cross-repo auth lands when aria-platform calls these endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException

from nexus.ontology import service
from nexus.ontology.exceptions import (
    GraphWriteError,
    ObjectNotFoundError,
    OntologyError,
    SchemaValidationError,
    TenantMismatchError,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["ontology"])


def _required(body: Dict[str, Any], field: str) -> Any:
    value = body.get(field)
    if value is None or (isinstance(value, str) and not value):
        raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
    return value


def _to_http_error(e: OntologyError) -> HTTPException:
    if isinstance(e, SchemaValidationError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, ObjectNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, TenantMismatchError):
        return HTTPException(status_code=403, detail=str(e))
    return HTTPException(status_code=500, detail=str(e))


@router.post("/propose_object")
async def propose_object_route(
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    """Create a new Loom object. Returns {object_id, version_id, action_event_id}."""
    object_type = _required(body, "object_type")
    tenant_id = _required(body, "tenant_id")
    actor = _required(body, "actor")
    properties = body.get("properties") or {}
    if not isinstance(properties, dict):
        raise HTTPException(status_code=400, detail="`properties` must be an object")

    try:
        result = service.propose_object(
            object_type=object_type,
            tenant_id=tenant_id,
            properties=properties,
            actor=actor,
            project_id=body.get("project_id"),
            source_conversation_id=body.get("source_conversation_id"),
        )
    except OntologyError as e:
        raise _to_http_error(e) from e

    return result


@router.post("/update_object")
async def update_object_route(
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    """Update an existing Loom object. Returns {object_id, version_id, action_event_id}."""
    object_type = _required(body, "object_type")
    object_id = _required(body, "object_id")
    tenant_id = _required(body, "tenant_id")
    actor = _required(body, "actor")
    change_reason = _required(body, "change_reason")
    updated_properties = body.get("updated_properties") or {}
    if not isinstance(updated_properties, dict):
        raise HTTPException(status_code=400, detail="`updated_properties` must be an object")

    try:
        result = service.update_object(
            object_type=object_type,
            object_id=object_id,
            tenant_id=tenant_id,
            updated_properties=updated_properties,
            actor=actor,
            change_reason=change_reason,
        )
    except OntologyError as e:
        raise _to_http_error(e) from e

    return result

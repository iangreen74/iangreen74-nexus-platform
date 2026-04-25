"""V2 ontology base class. Pattern mirrors nexus/ontology/schema.py:OntologyObject."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import ClassVar, Optional

from nexus.overwatch_v2.ontology.exceptions import V2SchemaValidationError
from nexus.overwatch_v2.ontology.types import NodeType, TENANT_ID


@dataclass
class V2OntologyObject:
    """Base for every V2 node. tenant_id is always 'overwatch-prime'."""
    id: str
    object_type: str
    version_id: int
    created_at: str
    valid_from: str
    created_by: str
    tenant_id: str = TENANT_ID
    valid_to: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ()
    EXPECTED_NODE_TYPE: ClassVar[Optional[str]] = None

    def __post_init__(self):
        if not self.id:
            raise V2SchemaValidationError("id is required")
        if self.tenant_id != TENANT_ID:
            raise V2SchemaValidationError(
                f"V2 tenant_id is locked to {TENANT_ID!r}, got {self.tenant_id!r}"
            )
        if not isinstance(self.version_id, int) or self.version_id < 1:
            raise V2SchemaValidationError(
                f"version_id must be int >= 1, got {self.version_id!r}"
            )
        if self.object_type not in NodeType.values():
            raise V2SchemaValidationError(
                f"object_type {self.object_type!r} not in {sorted(NodeType.values())}"
            )
        if self.EXPECTED_NODE_TYPE and self.object_type != self.EXPECTED_NODE_TYPE:
            raise V2SchemaValidationError(
                f"{type(self).__name__} requires object_type="
                f"{self.EXPECTED_NODE_TYPE!r}, got {self.object_type!r}"
            )
        for fname in self.REQUIRED_TYPE_FIELDS:
            if getattr(self, fname) in (None, ""):
                raise V2SchemaValidationError(
                    f"{type(self).__name__}.{fname} is required"
                )
        self._validate_type_specific()

    def _validate_type_specific(self):
        """Subclass hook for type-specific checks (status enums, ranges, etc)."""
        return None

    def to_neptune_props(self) -> dict:
        """Serialize for Cypher SET. Lists/dicts become JSON strings; Nones stripped."""
        out: dict = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if val is None:
                continue
            if isinstance(val, (list, dict)):
                out[f.name] = json.dumps(val)
            else:
                out[f.name] = val
        return out

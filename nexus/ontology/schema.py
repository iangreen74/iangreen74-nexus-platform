"""Loom v0 ontology object schemas.

Three dataclass types: Feature, Decision, Hypothesis. All inherit from
OntologyObject. Validation runs in __post_init__. to_neptune_props()
serializes for Cypher SET (lists become JSON strings, Nones stripped).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import ClassVar, List, Optional

from nexus.ontology.exceptions import SchemaValidationError
from nexus.ontology.types import (
    DecisionStatus, FeatureStatus, HypothesisStatus, ObjectType, Visibility,
)


@dataclass
class OntologyObject:
    id: str
    tenant_id: str
    version_id: int
    created_at: str
    updated_at: str
    created_by: str
    object_type: str
    project_id: Optional[str] = None
    visibility: str = Visibility.WORKSPACE.value

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ()
    EXPECTED_OBJECT_TYPE: ClassVar[Optional[str]] = None

    def __post_init__(self):
        self._validate_base()
        self._validate_type_specific()

    def _validate_base(self):
        if not self.id:
            raise SchemaValidationError("id is required")
        if not self.tenant_id:
            raise SchemaValidationError("tenant_id is required")
        if not isinstance(self.version_id, int) or self.version_id < 1:
            raise SchemaValidationError(f"version_id must be int >= 1, got {self.version_id!r}")
        if self.object_type not in ObjectType.values():
            raise SchemaValidationError(f"object_type {self.object_type!r} not in {sorted(ObjectType.values())}")
        if self.EXPECTED_OBJECT_TYPE and self.object_type != self.EXPECTED_OBJECT_TYPE:
            raise SchemaValidationError(f"{type(self).__name__} requires object_type={self.EXPECTED_OBJECT_TYPE!r}, got {self.object_type!r}")
        if self.visibility not in Visibility.values():
            raise SchemaValidationError(f"visibility {self.visibility!r} invalid")

    def _validate_type_specific(self):
        for fname in self.REQUIRED_TYPE_FIELDS:
            if not getattr(self, fname):
                raise SchemaValidationError(f"{type(self).__name__}.{fname} is required")

    def to_neptune_props(self) -> dict:
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if val is None:
                continue
            out[f.name] = json.dumps(val) if isinstance(val, list) else val
        return out


@dataclass
class Feature(OntologyObject):
    name: str = ""
    description: str = ""
    status: str = FeatureStatus.PROPOSED.value
    shipped_at: Optional[str] = None
    deprecated_at: Optional[str] = None
    reason_for_deprecation: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "description", "project_id")
    EXPECTED_OBJECT_TYPE: ClassVar[str] = ObjectType.FEATURE.value

    def _validate_type_specific(self):
        super()._validate_type_specific()
        if self.status not in FeatureStatus.values():
            raise SchemaValidationError(f"Feature.status {self.status!r} not in {sorted(FeatureStatus.values())}")


@dataclass
class Decision(OntologyObject):
    name: str = ""
    context: str = ""
    alternatives_considered: List[str] = field(default_factory=list)
    choice_made: str = ""
    reasoning: str = ""
    decided_at: str = ""
    decided_by: str = ""
    status: str = DecisionStatus.ACTIVE.value

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "context", "choice_made", "reasoning", "decided_at", "decided_by")
    EXPECTED_OBJECT_TYPE: ClassVar[str] = ObjectType.DECISION.value

    def _validate_type_specific(self):
        super()._validate_type_specific()
        if self.status not in DecisionStatus.values():
            raise SchemaValidationError(f"Decision.status {self.status!r} not in {sorted(DecisionStatus.values())}")


@dataclass
class Hypothesis(OntologyObject):
    statement: str = ""
    why_believed: str = ""
    how_will_be_tested: str = ""
    status: str = HypothesisStatus.UNVALIDATED.value
    confirmed_at: Optional[str] = None
    falsified_at: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("statement", "why_believed", "how_will_be_tested")
    EXPECTED_OBJECT_TYPE: ClassVar[str] = ObjectType.HYPOTHESIS.value

    def _validate_type_specific(self):
        super()._validate_type_specific()
        if self.status not in HypothesisStatus.values():
            raise SchemaValidationError(f"Hypothesis.status {self.status!r} not in {sorted(HypothesisStatus.values())}")


OBJECT_TYPE_REGISTRY = {
    ObjectType.FEATURE.value: Feature,
    ObjectType.DECISION.value: Decision,
    ObjectType.HYPOTHESIS.value: Hypothesis,
}


def object_class_for(object_type: str):
    cls = OBJECT_TYPE_REGISTRY.get(object_type)
    if cls is None:
        raise SchemaValidationError(f"Unknown object_type {object_type!r}; known: {sorted(OBJECT_TYPE_REGISTRY)}")
    return cls

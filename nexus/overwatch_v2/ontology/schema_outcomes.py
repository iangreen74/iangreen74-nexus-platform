"""V2 ontology dataclasses (part 2) — 6 types per spec §6.3.

Outcome / conversation surface: Pattern, Failure, Success, CapabilityState,
Conversation, ConversationTurn.

First 7 types live in schema_objects.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, List, Optional

from nexus.overwatch_v2.ontology.exceptions import V2SchemaValidationError
from nexus.overwatch_v2.ontology.schema_base import V2OntologyObject
from nexus.overwatch_v2.ontology.types import (
    AutonomyLevel, ConversationStatus, NodeType, TurnRole,
)


@dataclass
class Pattern(V2OntologyObject):
    name: str = ""
    signature: dict = field(default_factory=dict)
    fix: str = ""
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "fix")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.PATTERN.value

    def _validate_type_specific(self):
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise V2SchemaValidationError("Pattern.confidence must be 0..1")


@dataclass
class Failure(V2OntologyObject):
    what: str = ""
    root_cause: Optional[str] = None
    blast_radius: List[str] = field(default_factory=list)
    resolution: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("what",)
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.FAILURE.value


@dataclass
class Success(V2OntologyObject):
    what: str = ""
    method: str = ""
    reusability: str = ""

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("what", "method")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.SUCCESS.value


@dataclass
class CapabilityState(V2OntologyObject):
    capability_name: str = ""
    autonomy_level: str = AutonomyLevel.L1_REACTIVE.value
    last_exercised: Optional[str] = None
    success_rate_30d: float = 0.0

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("capability_name",)
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.CAPABILITY_STATE.value

    def _validate_type_specific(self):
        if self.autonomy_level not in AutonomyLevel.values():
            raise V2SchemaValidationError(
                f"CapabilityState.autonomy_level {self.autonomy_level!r} invalid"
            )
        if not 0.0 <= float(self.success_rate_30d) <= 1.0:
            raise V2SchemaValidationError("CapabilityState.success_rate_30d must be 0..1")


@dataclass
class Conversation(V2OntologyObject):
    title: str = ""
    started_at: str = ""
    last_active_at: str = ""
    turn_count: int = 0
    status: str = ConversationStatus.ACTIVE.value
    tags: List[str] = field(default_factory=list)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("title", "started_at", "last_active_at")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.CONVERSATION.value

    def _validate_type_specific(self):
        if self.status not in ConversationStatus.values():
            raise V2SchemaValidationError(f"Conversation.status {self.status!r} invalid")


@dataclass
class ConversationTurn(V2OntologyObject):
    conversation_id: str = ""
    role: str = TurnRole.USER.value
    content: str = ""
    tool_calls: List[Any] = field(default_factory=list)
    timestamp: str = ""

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("conversation_id", "content", "timestamp")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.CONVERSATION_TURN.value

    def _validate_type_specific(self):
        if self.role not in TurnRole.values():
            raise V2SchemaValidationError(f"ConversationTurn.role {self.role!r} invalid")

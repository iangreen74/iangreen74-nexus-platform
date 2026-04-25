"""V2 ontology type enums — node types, edge types, status enums.

Per docs/OVERWATCH_V2_SPECIFICATION.md §6.3. 13 node types, 15 edge types.
"""
from __future__ import annotations
from enum import Enum


class _ValuesMixin:
    @classmethod
    def values(cls) -> set:
        return {member.value for member in cls}


class NodeType(_ValuesMixin, str, Enum):
    ENGINEERING_TASK = "EngineeringTask"
    INVESTIGATION = "Investigation"
    HYPOTHESIS = "Hypothesis"
    EVIDENCE = "Evidence"
    DECISION = "Decision"
    FIX_ATTEMPT = "FixAttempt"
    DEPLOY_EVENT = "DeployEvent"
    PATTERN = "Pattern"
    FAILURE = "Failure"
    SUCCESS = "Success"
    CAPABILITY_STATE = "CapabilityState"
    CONVERSATION = "Conversation"
    CONVERSATION_TURN = "ConversationTurn"


class EdgeType(_ValuesMixin, str, Enum):
    INVESTIGATES = "INVESTIGATES"
    PRODUCES = "PRODUCES"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    RESOLVED_BY = "RESOLVED_BY"
    CAUSED_BY = "CAUSED_BY"
    TARGETS = "TARGETS"
    COMMITS = "COMMITS"
    DEPLOYED_VIA = "DEPLOYED_VIA"
    LEARNED_FROM = "LEARNED_FROM"
    APPLIES_TO = "APPLIES_TO"
    RESULTED_IN = "RESULTED_IN"
    DECIDED = "DECIDED"
    TURNED_INTO = "TURNED_INTO"
    EXERCISES = "EXERCISES"


class TaskStatus(_ValuesMixin, str, Enum):
    PROPOSED = "proposed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class TaskPriority(_ValuesMixin, str, Enum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"


class InvestigationVerdict(_ValuesMixin, str, Enum):
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class HypothesisStatus(_ValuesMixin, str, Enum):
    UNTESTED = "untested"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"


class Reversibility(_ValuesMixin, str, Enum):
    REVERSIBLE = "reversible"
    ONE_WAY = "one_way"


class FixOutcome(_ValuesMixin, str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class DeployStatus(_ValuesMixin, str, Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class AutonomyLevel(_ValuesMixin, str, Enum):
    L1_REACTIVE = "L1"
    L2_GUIDED = "L2"
    L3_PROACTIVE = "L3"
    L4_ANTICIPATORY = "L4"
    L5_INVISIBLE = "L5"


class ConversationStatus(_ValuesMixin, str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TurnRole(_ValuesMixin, str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


TENANT_ID = "overwatch-prime"

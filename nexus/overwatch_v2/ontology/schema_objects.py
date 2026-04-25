"""V2 ontology dataclasses (part 1) — 7 types per spec §6.3.

Engineering / investigation surface: EngineeringTask, Investigation,
Hypothesis, Evidence, Decision, FixAttempt, DeployEvent.

Remaining 6 types (Pattern, Failure, Success, CapabilityState,
Conversation, ConversationTurn) live in schema_outcomes.py to respect
the 200-line per-file ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List, Optional

from nexus.overwatch_v2.ontology.exceptions import V2SchemaValidationError
from nexus.overwatch_v2.ontology.schema_base import V2OntologyObject
from nexus.overwatch_v2.ontology.types import (
    DeployStatus, FixOutcome, HypothesisStatus, InvestigationVerdict,
    NodeType, Reversibility, TaskPriority, TaskStatus,
)


@dataclass
class EngineeringTask(V2OntologyObject):
    title: str = ""
    description: str = ""
    status: str = TaskStatus.PROPOSED.value
    priority: str = TaskPriority.P2.value
    completed_at: Optional[str] = None
    thread_id: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("title", "description")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.ENGINEERING_TASK.value

    def _validate_type_specific(self):
        if self.status not in TaskStatus.values():
            raise V2SchemaValidationError(f"EngineeringTask.status {self.status!r} invalid")
        if self.priority not in TaskPriority.values():
            raise V2SchemaValidationError(f"EngineeringTask.priority {self.priority!r} invalid")


@dataclass
class Investigation(V2OntologyObject):
    hypothesis: str = ""
    methodology: str = ""
    tools_used: List[str] = field(default_factory=list)
    duration_seconds: int = 0
    verdict: str = InvestigationVerdict.INCONCLUSIVE.value
    confidence: float = 0.0

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("hypothesis", "methodology")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.INVESTIGATION.value

    def _validate_type_specific(self):
        if self.verdict not in InvestigationVerdict.values():
            raise V2SchemaValidationError(f"Investigation.verdict {self.verdict!r} invalid")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise V2SchemaValidationError("Investigation.confidence must be 0..1")


@dataclass
class Hypothesis(V2OntologyObject):
    claim: str = ""
    status: str = HypothesisStatus.UNTESTED.value
    evidence_for: List[str] = field(default_factory=list)
    evidence_against: List[str] = field(default_factory=list)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("claim",)
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.HYPOTHESIS.value

    def _validate_type_specific(self):
        if self.status not in HypothesisStatus.values():
            raise V2SchemaValidationError(f"Hypothesis.status {self.status!r} invalid")


@dataclass
class Evidence(V2OntologyObject):
    source: str = ""
    observation: str = ""
    timestamp: str = ""
    raw: dict = field(default_factory=dict)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("source", "observation", "timestamp")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.EVIDENCE.value


@dataclass
class Decision(V2OntologyObject):
    question: str = ""
    options_considered: List[str] = field(default_factory=list)
    chosen: str = ""
    rationale: str = ""
    reversibility: str = Reversibility.REVERSIBLE.value

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("question", "chosen", "rationale")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DECISION.value

    def _validate_type_specific(self):
        if self.reversibility not in Reversibility.values():
            raise V2SchemaValidationError(f"Decision.reversibility {self.reversibility!r} invalid")


@dataclass
class FixAttempt(V2OntologyObject):
    task_id: str = ""
    description: str = ""
    commits: List[str] = field(default_factory=list)
    mutations: List[str] = field(default_factory=list)
    outcome: str = FixOutcome.PARTIAL.value

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("task_id", "description")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.FIX_ATTEMPT.value

    def _validate_type_specific(self):
        if self.outcome not in FixOutcome.values():
            raise V2SchemaValidationError(f"FixAttempt.outcome {self.outcome!r} invalid")


@dataclass
class DeployEvent(V2OntologyObject):
    repo: str = ""
    sfn_execution_arn: Optional[str] = None
    status: str = DeployStatus.STARTED.value
    duration_seconds: int = 0
    resources_created: List[str] = field(default_factory=list)
    resources_failed: List[str] = field(default_factory=list)
    sfn_output: dict = field(default_factory=dict)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("repo",)
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DEPLOY_EVENT.value

    def _validate_type_specific(self):
        if self.status not in DeployStatus.values():
            raise V2SchemaValidationError(f"DeployEvent.status {self.status!r} invalid")

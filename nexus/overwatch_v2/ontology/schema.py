"""V2 ontology schema — public surface.

Re-exports node dataclasses, edge validation, and the type registry.
Class definitions are split across schema_objects.py (7 engineering),
schema_outcomes.py (6 outcomes/conversations), and schema_aws.py
(8 AWS-catalog) to respect the 200-line per-file ceiling.
"""
from __future__ import annotations

from nexus.overwatch_v2.ontology.exceptions import (
    V2EdgeValidationError, V2OntologyError, V2SchemaValidationError,
)
from nexus.overwatch_v2.ontology.schema_base import V2OntologyObject
from nexus.overwatch_v2.ontology.schema_edges import EDGE_RULES, validate_edge
from nexus.overwatch_v2.ontology.schema_objects import (
    Decision, DeployEvent, EngineeringTask, Evidence, FixAttempt,
    Hypothesis, Investigation,
)
from nexus.overwatch_v2.ontology.schema_outcomes import (
    CapabilityState, Conversation, ConversationTurn, Failure, Pattern, Success,
)
from nexus.overwatch_v2.ontology.schema_aws import (
    Database, DataStore, Deployment, DeploymentTarget, Infrastructure,
    Runner, Service, WorkerNode,
)
from nexus.overwatch_v2.ontology.types import EdgeType, NodeType, TENANT_ID


OBJECT_TYPE_REGISTRY = {
    NodeType.ENGINEERING_TASK.value: EngineeringTask,
    NodeType.INVESTIGATION.value: Investigation,
    NodeType.HYPOTHESIS.value: Hypothesis,
    NodeType.EVIDENCE.value: Evidence,
    NodeType.DECISION.value: Decision,
    NodeType.FIX_ATTEMPT.value: FixAttempt,
    NodeType.DEPLOY_EVENT.value: DeployEvent,
    NodeType.PATTERN.value: Pattern,
    NodeType.FAILURE.value: Failure,
    NodeType.SUCCESS.value: Success,
    NodeType.CAPABILITY_STATE.value: CapabilityState,
    NodeType.CONVERSATION.value: Conversation,
    NodeType.CONVERSATION_TURN.value: ConversationTurn,
    # Track Q AWS catalog types
    NodeType.SERVICE.value: Service,
    NodeType.DATABASE.value: Database,
    NodeType.DATA_STORE.value: DataStore,
    NodeType.INFRASTRUCTURE.value: Infrastructure,
    NodeType.RUNNER.value: Runner,
    NodeType.WORKER_NODE.value: WorkerNode,
    NodeType.DEPLOYMENT.value: Deployment,
    NodeType.DEPLOYMENT_TARGET.value: DeploymentTarget,
}


def object_class_for(object_type: str):
    cls = OBJECT_TYPE_REGISTRY.get(object_type)
    if cls is None:
        raise V2SchemaValidationError(
            f"Unknown object_type {object_type!r}; "
            f"known: {sorted(OBJECT_TYPE_REGISTRY)}"
        )
    return cls


__all__ = [
    "V2OntologyObject", "object_class_for",
    "OBJECT_TYPE_REGISTRY", "EDGE_RULES", "validate_edge",
    "EdgeType", "NodeType", "TENANT_ID",
    "V2OntologyError", "V2SchemaValidationError", "V2EdgeValidationError",
    "CapabilityState", "Conversation", "ConversationTurn", "Decision",
    "DeployEvent", "EngineeringTask", "Evidence", "Failure", "FixAttempt",
    "Hypothesis", "Investigation", "Pattern", "Success",
    "Database", "DataStore", "Deployment", "DeploymentTarget",
    "Infrastructure", "Runner", "Service", "WorkerNode",
]

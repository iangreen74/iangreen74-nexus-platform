"""Overwatch V2 ontology — engineering-object schema, dual-write service layer.

Single canonical write path for V2's OverwatchGraph + OverwatchPostgres.
21 node types (13 from §6.3 + 8 from Track Q AWS-catalog expansion),
15 edge types.
"""
from nexus.overwatch_v2.ontology.exceptions import (
    V2EdgeValidationError, V2GraphWriteError, V2ObjectNotFoundError,
    V2OntologyError, V2PostgresNotConfiguredError, V2SchemaValidationError,
)
from nexus.overwatch_v2.ontology.schema import (
    EDGE_RULES, NodeType, EdgeType, OBJECT_TYPE_REGISTRY, TENANT_ID,
    V2OntologyObject, object_class_for, validate_edge,
    CapabilityState, Conversation, ConversationTurn, Decision, DeployEvent,
    EngineeringTask, Evidence, Failure, FixAttempt, Hypothesis,
    Investigation, Pattern, Success,
    Database, DataStore, Deployment, DeploymentTarget, Infrastructure,
    Runner, Service, WorkerNode,
)
from nexus.overwatch_v2.ontology.service import (
    create_link, get_object, list_objects_by_type, propose_object,
    query, update_object,
)

__all__ = [
    "TENANT_ID", "NodeType", "EdgeType", "OBJECT_TYPE_REGISTRY",
    "EDGE_RULES", "validate_edge", "object_class_for",
    "V2OntologyObject", "V2OntologyError", "V2SchemaValidationError",
    "V2EdgeValidationError", "V2ObjectNotFoundError",
    "V2GraphWriteError", "V2PostgresNotConfiguredError",
    "CapabilityState", "Conversation", "ConversationTurn", "Decision",
    "DeployEvent", "EngineeringTask", "Evidence", "Failure", "FixAttempt",
    "Hypothesis", "Investigation", "Pattern", "Success",
    "Database", "DataStore", "Deployment", "DeploymentTarget",
    "Infrastructure", "Runner", "Service", "WorkerNode",
    "propose_object", "update_object", "create_link",
    "get_object", "list_objects_by_type", "query",
]

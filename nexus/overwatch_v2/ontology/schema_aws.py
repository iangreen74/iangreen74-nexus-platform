"""V2 ontology dataclasses (part 3) — 8 AWS-catalog node types.

Track Q expansion. Adds the catalog-level types Echo needs to answer
"list all X" / "how many X" questions: Service, Database, DataStore,
Infrastructure, Runner, WorkerNode, Deployment, DeploymentTarget.

All additive; existing types in schema_objects.py + schema_outcomes.py
unchanged. Pattern and DeployEvent (existing) remain semantically
distinct from the new Deployment (Pattern = recurring shape; DeployEvent
= single pipeline run; Deployment = catalog entry of a currently-
deployed thing).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, List, Optional

from nexus.overwatch_v2.ontology.schema_base import V2OntologyObject
from nexus.overwatch_v2.ontology.types import NodeType


@dataclass
class Service(V2OntologyObject):
    name: str = ""
    kind: str = ""  # ecs_cluster | ecs_service | lambda | step_function | other
    status: Optional[str] = None
    region: Optional[str] = None
    account_id: Optional[str] = None
    arn: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "kind")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.SERVICE.value


@dataclass
class Database(V2OntologyObject):
    name: str = ""
    engine: Optional[str] = None  # postgres | mysql | aurora-postgres | etc
    instance_class: Optional[str] = None
    status: Optional[str] = None
    endpoint: Optional[str] = None
    region: Optional[str] = None
    arn: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name",)
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DATABASE.value


@dataclass
class DataStore(V2OntologyObject):
    name: str = ""
    kind: str = ""  # s3 | dynamodb | neptune-graph | redshift | other
    status: Optional[str] = None
    region: Optional[str] = None
    arn: Optional[str] = None
    extra: dict = field(default_factory=dict)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "kind")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DATA_STORE.value


@dataclass
class Infrastructure(V2OntologyObject):
    name: str = ""
    kind: str = ""  # vpc | alb | nlb | target_group | security_group | subnet | route_table
    status: Optional[str] = None
    region: Optional[str] = None
    arn: Optional[str] = None
    parent_id: Optional[str] = None  # e.g., a target_group's parent ALB ARN

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "kind")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.INFRASTRUCTURE.value


@dataclass
class Runner(V2OntologyObject):
    name: str = ""
    kind: str = ""  # github_actions_runner | codebuild | ecs_one_off | other
    status: Optional[str] = None
    host_instance: Optional[str] = None  # EC2 instance ID for self-hosted runners
    region: Optional[str] = None
    labels: List[str] = field(default_factory=list)

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "kind")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.RUNNER.value


@dataclass
class WorkerNode(V2OntologyObject):
    name: str = ""
    instance_id: str = ""
    role: Optional[str] = None  # runner-host | dogfood-worker | other
    ami_id: Optional[str] = None
    instance_type: Optional[str] = None
    status: Optional[str] = None
    region: Optional[str] = None
    private_ip: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "instance_id")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.WORKER_NODE.value


@dataclass
class Deployment(V2OntologyObject):
    service_name: str = ""
    revision: Optional[str] = None
    image_uri: Optional[str] = None
    status: Optional[str] = None  # CREATE_COMPLETE | UPDATE_IN_PROGRESS | etc
    deployed_at: Optional[str] = None
    region: Optional[str] = None
    name: str = ""  # Stack/deploy ID for catalog lookup

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("service_name", "name")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DEPLOYMENT.value


@dataclass
class DeploymentTarget(V2OntologyObject):
    name: str = ""
    kind: str = ""  # ecs_service | lambda_alias | s3_static_site | etc
    service_ref: Optional[str] = None  # logical ref to the parent Service object
    region: Optional[str] = None
    status: Optional[str] = None

    REQUIRED_TYPE_FIELDS: ClassVar[tuple] = ("name", "kind")
    EXPECTED_NODE_TYPE: ClassVar[str] = NodeType.DEPLOYMENT_TARGET.value

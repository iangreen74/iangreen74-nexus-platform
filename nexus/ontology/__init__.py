"""
Loom — the v0 ontological intelligence for Forgewing.

The ontology is Forgewing's compounding primitive per docs/STARTUP_ONTOLOGY.md.
This package holds the schema, graph write layer, service actions, and
(forthcoming) API routes for Loom.

v0 scope:
    3 object types: Feature, Decision, Hypothesis
    3 link types:   motivates, supersedes, validates
    2 actions:      propose_object, update_object

Postgres version history lands in a follow-up commit (needs RDS + psycopg2).
S3/Iceberg ActionEvent corpus lands in a subsequent commit.
API routes land in the next nexus prompt.
"""

from nexus.ontology.exceptions import (
    GraphWriteError,
    ObjectNotFoundError,
    OntologyError,
    SchemaValidationError,
    TenantMismatchError,
    VersionConflictError,
)
from nexus.ontology.schema import (
    Decision,
    Feature,
    Hypothesis,
    OntologyObject,
    object_class_for,
)
from nexus.ontology.types import (
    DecisionStatus,
    FeatureStatus,
    HypothesisStatus,
    LinkType,
    ObjectType,
    Visibility,
)

__all__ = [
    "Decision", "DecisionStatus", "Feature", "FeatureStatus",
    "GraphWriteError", "Hypothesis", "HypothesisStatus",
    "LinkType", "ObjectNotFoundError", "ObjectType",
    "OntologyError", "OntologyObject", "SchemaValidationError",
    "TenantMismatchError", "VersionConflictError", "Visibility",
    "object_class_for",
]

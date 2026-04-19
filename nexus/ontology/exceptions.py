"""Typed exceptions for Loom v0."""


class OntologyError(Exception):
    """Base class for all Loom ontology errors."""


class SchemaValidationError(OntologyError):
    """An object's shape violates the v0 schema."""


class TenantMismatchError(OntologyError):
    """An operation crossed tenant boundaries."""


class ObjectNotFoundError(OntologyError):
    """An object was referenced but does not exist."""


class VersionConflictError(OntologyError):
    """An update was attempted against a stale version."""


class GraphWriteError(OntologyError):
    """Neptune write failed."""

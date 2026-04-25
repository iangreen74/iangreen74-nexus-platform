"""Typed exceptions for V2 ontology. Mirrors nexus/ontology/exceptions.py shape."""


class V2OntologyError(Exception):
    """Base class for V2 ontology errors."""


class V2SchemaValidationError(V2OntologyError):
    """An object's shape violates the V2 schema."""


class V2ObjectNotFoundError(V2OntologyError):
    """An object was referenced but does not exist."""


class V2EdgeValidationError(V2OntologyError):
    """An edge's source/target types don't match the edge's allowed shape."""


class V2GraphWriteError(V2OntologyError):
    """Neptune write failed."""


class V2PostgresNotConfiguredError(RuntimeError):
    """OVERWATCH_V2_DATABASE_URL not set."""

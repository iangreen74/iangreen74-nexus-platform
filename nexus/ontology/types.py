"""Type enums for Loom v0. Per docs/STARTUP_ONTOLOGY.md Sections 7-9."""
from __future__ import annotations
from enum import Enum


class _ValuesMixin:
    @classmethod
    def values(cls) -> set:
        return {member.value for member in cls}


class ObjectType(_ValuesMixin, str, Enum):
    FEATURE = "Feature"
    DECISION = "Decision"
    HYPOTHESIS = "Hypothesis"


class LinkType(_ValuesMixin, str, Enum):
    MOTIVATES = "motivates"
    SUPERSEDES = "supersedes"
    VALIDATES = "validates"


class FeatureStatus(_ValuesMixin, str, Enum):
    PROPOSED = "proposed"
    IN_PROGRESS = "in_progress"
    SHIPPED = "shipped"
    DEPRECATED = "deprecated"
    CANCELLED = "cancelled"


class DecisionStatus(_ValuesMixin, str, Enum):
    ACTIVE = "active"
    REVISED = "revised"
    REVERSED = "reversed"


class HypothesisStatus(_ValuesMixin, str, Enum):
    UNVALIDATED = "unvalidated"
    VALIDATING = "validating"
    CONFIRMED = "confirmed"
    FALSIFIED = "falsified"
    ABANDONED = "abandoned"


class Visibility(_ValuesMixin, str, Enum):
    OWNER_ONLY = "owner_only"
    WORKSPACE = "workspace"
    WORKSPACE_READ_OWNER_WRITE = "workspace_read_owner_write"

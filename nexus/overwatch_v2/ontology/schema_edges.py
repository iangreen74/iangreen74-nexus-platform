"""V2 edge type validation — 15 edges per spec §6.3.

Edges with typed targets (other ontology nodes) validate type pairing.
Edges with free-form targets (resource_id, git_sha, regex, list[Failure])
accept anything.
"""
from __future__ import annotations

from typing import Optional

from nexus.overwatch_v2.ontology.exceptions import V2EdgeValidationError
from nexus.overwatch_v2.ontology.types import EdgeType, NodeType


# (edge_type) -> (allowed_source_types, allowed_target_types or None for free)
# None means target is free-form (resource_id, git_sha, regex pattern, etc).
EDGE_RULES: dict = {
    EdgeType.INVESTIGATES.value: (
        {NodeType.ENGINEERING_TASK.value},
        {NodeType.INVESTIGATION.value},
    ),
    EdgeType.PRODUCES.value: (
        {NodeType.INVESTIGATION.value},
        {NodeType.HYPOTHESIS.value},
    ),
    EdgeType.SUPPORTS.value: (
        {NodeType.EVIDENCE.value},
        {NodeType.HYPOTHESIS.value},
    ),
    EdgeType.CONTRADICTS.value: (
        {NodeType.EVIDENCE.value},
        {NodeType.HYPOTHESIS.value},
    ),
    EdgeType.RESOLVED_BY.value: (
        {NodeType.FAILURE.value},
        {NodeType.FIX_ATTEMPT.value},
    ),
    EdgeType.CAUSED_BY.value: (
        {NodeType.FAILURE.value},
        None,  # resource_id — free-form
    ),
    EdgeType.TARGETS.value: (
        {NodeType.FIX_ATTEMPT.value},
        None,  # resource_id — free-form
    ),
    EdgeType.COMMITS.value: (
        {NodeType.FIX_ATTEMPT.value},
        None,  # git_sha — free-form
    ),
    EdgeType.DEPLOYED_VIA.value: (
        {NodeType.FIX_ATTEMPT.value},
        {NodeType.DEPLOY_EVENT.value},
    ),
    EdgeType.LEARNED_FROM.value: (
        {NodeType.PATTERN.value},
        {NodeType.FAILURE.value},
    ),
    EdgeType.APPLIES_TO.value: (
        {NodeType.PATTERN.value},
        None,  # resource_pattern — free-form
    ),
    EdgeType.RESULTED_IN.value: (
        {NodeType.FIX_ATTEMPT.value},
        {NodeType.SUCCESS.value, NodeType.FAILURE.value},
    ),
    EdgeType.DECIDED.value: (
        {NodeType.CONVERSATION.value},
        {NodeType.DECISION.value},
    ),
    EdgeType.TURNED_INTO.value: (
        {NodeType.CONVERSATION.value},
        {NodeType.ENGINEERING_TASK.value},
    ),
    EdgeType.EXERCISES.value: (
        {NodeType.ENGINEERING_TASK.value},
        {NodeType.CAPABILITY_STATE.value},
    ),
}


def validate_edge(edge_type: str, from_type: str, to_type: Optional[str]) -> None:
    """Raise V2EdgeValidationError if the edge's source/target types are invalid.

    to_type may be None for edges with free-form targets (resource_id etc).
    """
    if edge_type not in EDGE_RULES:
        raise V2EdgeValidationError(
            f"Unknown edge_type {edge_type!r}; allowed: {sorted(EDGE_RULES)}"
        )
    allowed_sources, allowed_targets = EDGE_RULES[edge_type]
    if from_type not in allowed_sources:
        raise V2EdgeValidationError(
            f"Edge {edge_type}: source must be one of {sorted(allowed_sources)}, "
            f"got {from_type!r}"
        )
    if allowed_targets is not None:
        if to_type is None:
            raise V2EdgeValidationError(
                f"Edge {edge_type}: target type required (one of {sorted(allowed_targets)})"
            )
        if to_type not in allowed_targets:
            raise V2EdgeValidationError(
                f"Edge {edge_type}: target must be one of {sorted(allowed_targets)}, "
                f"got {to_type!r}"
            )

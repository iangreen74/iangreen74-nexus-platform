"""Schema and edge-validation tests for the V2 ontology.

NEXUS_MODE=local; no real Postgres or Neptune touched here.
"""
import os
os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus.overwatch_v2.ontology import (  # noqa: E402
    EngineeringTask, Investigation, Hypothesis, Evidence, Decision,
    FixAttempt, DeployEvent, Pattern, Failure, Success, CapabilityState,
    Conversation, ConversationTurn,
    NodeType, EdgeType, OBJECT_TYPE_REGISTRY, EDGE_RULES,
    V2SchemaValidationError, V2EdgeValidationError,
    object_class_for, validate_edge,
)


def _common_kwargs(node_type: str) -> dict:
    return {
        "id": "test-id-1", "object_type": node_type, "version_id": 1,
        "created_at": "2026-04-24T00:00:00+00:00",
        "valid_from": "2026-04-24T00:00:00+00:00",
        "created_by": "test",
    }


# --- Registry & enum coverage ------------------------------------------------

def test_registry_has_21_entries():
    # 13 from §6.3 + 8 from Track Q AWS-catalog expansion
    assert len(OBJECT_TYPE_REGISTRY) == 21


def test_node_type_enum_has_21_members():
    assert len(NodeType.values()) == 21


def test_edge_type_enum_has_15_members():
    assert len(EdgeType.values()) == 15


def test_edge_rules_cover_all_edge_types():
    assert set(EDGE_RULES.keys()) == EdgeType.values()


def test_object_class_for_each_type_resolvable():
    for nt in NodeType.values():
        assert object_class_for(nt) is OBJECT_TYPE_REGISTRY[nt]


def test_object_class_for_unknown_type_raises():
    with pytest.raises(V2SchemaValidationError):
        object_class_for("NotARealType")


# --- Per-type happy / sad path ---------------------------------------------

def test_engineering_task_valid():
    t = EngineeringTask(**_common_kwargs("EngineeringTask"),
                        title="t", description="d")
    assert t.status == "proposed"


def test_engineering_task_missing_title_raises():
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**_common_kwargs("EngineeringTask"), description="d")


def test_engineering_task_invalid_status_raises():
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**_common_kwargs("EngineeringTask"),
                        title="t", description="d", status="bogus")


def test_engineering_task_invalid_priority_raises():
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**_common_kwargs("EngineeringTask"),
                        title="t", description="d", priority="px")


def test_investigation_valid_and_confidence_bounds():
    i = Investigation(**_common_kwargs("Investigation"),
                      hypothesis="h", methodology="m", confidence=0.5)
    assert i.confidence == 0.5


def test_investigation_confidence_out_of_range():
    with pytest.raises(V2SchemaValidationError):
        Investigation(**_common_kwargs("Investigation"),
                      hypothesis="h", methodology="m", confidence=2.0)


def test_investigation_invalid_verdict():
    with pytest.raises(V2SchemaValidationError):
        Investigation(**_common_kwargs("Investigation"),
                      hypothesis="h", methodology="m", verdict="??")


def test_hypothesis_valid_default_status():
    h = Hypothesis(**_common_kwargs("Hypothesis"), claim="c")
    assert h.status == "untested"


def test_hypothesis_missing_claim_raises():
    with pytest.raises(V2SchemaValidationError):
        Hypothesis(**_common_kwargs("Hypothesis"))


def test_hypothesis_invalid_status():
    with pytest.raises(V2SchemaValidationError):
        Hypothesis(**_common_kwargs("Hypothesis"), claim="c", status="weird")


def test_evidence_valid():
    e = Evidence(**_common_kwargs("Evidence"),
                 source="aws.cli", observation="found", timestamp="2026-04-24")
    assert e.source == "aws.cli"


def test_evidence_missing_required():
    with pytest.raises(V2SchemaValidationError):
        Evidence(**_common_kwargs("Evidence"), source="aws.cli", observation="x")


def test_decision_valid():
    d = Decision(**_common_kwargs("Decision"),
                 question="q?", chosen="a", rationale="r")
    assert d.reversibility == "reversible"


def test_decision_invalid_reversibility():
    with pytest.raises(V2SchemaValidationError):
        Decision(**_common_kwargs("Decision"),
                 question="q?", chosen="a", rationale="r", reversibility="oops")


def test_fixattempt_valid():
    f = FixAttempt(**_common_kwargs("FixAttempt"), task_id="t", description="d")
    assert f.outcome == "partial"


def test_fixattempt_invalid_outcome():
    with pytest.raises(V2SchemaValidationError):
        FixAttempt(**_common_kwargs("FixAttempt"),
                   task_id="t", description="d", outcome="zzz")


def test_deployevent_valid_default_status():
    de = DeployEvent(**_common_kwargs("DeployEvent"), repo="ig/x")
    assert de.status == "started"


def test_deployevent_invalid_status():
    with pytest.raises(V2SchemaValidationError):
        DeployEvent(**_common_kwargs("DeployEvent"), repo="ig/x", status="??")


def test_pattern_valid_and_confidence_bounds():
    p = Pattern(**_common_kwargs("Pattern"), name="n", fix="f", confidence=0.99)
    assert p.confidence == 0.99


def test_pattern_confidence_out_of_range():
    with pytest.raises(V2SchemaValidationError):
        Pattern(**_common_kwargs("Pattern"), name="n", fix="f", confidence=-0.1)


def test_failure_valid_minimum():
    f = Failure(**_common_kwargs("Failure"), what="VerifyHealth crashed")
    assert f.root_cause is None


def test_failure_missing_what():
    with pytest.raises(V2SchemaValidationError):
        Failure(**_common_kwargs("Failure"))


def test_success_valid():
    s = Success(**_common_kwargs("Success"),
                what="deploy ok", method="manual probe", reusability="any")
    assert s.method == "manual probe"


def test_success_requires_what_and_method():
    with pytest.raises(V2SchemaValidationError):
        Success(**_common_kwargs("Success"), what="ok")


def test_capability_state_valid():
    c = CapabilityState(**_common_kwargs("CapabilityState"),
                        capability_name="restart_ecs", autonomy_level="L3")
    assert c.autonomy_level == "L3"


def test_capability_state_invalid_autonomy():
    with pytest.raises(V2SchemaValidationError):
        CapabilityState(**_common_kwargs("CapabilityState"),
                        capability_name="x", autonomy_level="L9")


def test_capability_state_success_rate_out_of_range():
    with pytest.raises(V2SchemaValidationError):
        CapabilityState(**_common_kwargs("CapabilityState"),
                        capability_name="x", success_rate_30d=1.5)


def test_conversation_valid():
    c = Conversation(**_common_kwargs("Conversation"),
                     title="t", started_at="ts", last_active_at="ts")
    assert c.status == "active"


def test_conversation_invalid_status():
    with pytest.raises(V2SchemaValidationError):
        Conversation(**_common_kwargs("Conversation"),
                     title="t", started_at="ts", last_active_at="ts",
                     status="??")


def test_conversation_turn_valid():
    ct = ConversationTurn(**_common_kwargs("ConversationTurn"),
                          conversation_id="conv-1", content="hi", timestamp="ts")
    assert ct.role == "user"


def test_conversation_turn_invalid_role():
    with pytest.raises(V2SchemaValidationError):
        ConversationTurn(**_common_kwargs("ConversationTurn"),
                         conversation_id="c", content="hi", timestamp="ts",
                         role="overlord")


# --- Base validation -------------------------------------------------------

def test_tenant_id_must_be_overwatch_prime():
    kw = _common_kwargs("EngineeringTask")
    kw["tenant_id"] = "wrong-tenant"
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**kw, title="t", description="d")


def test_version_id_must_be_positive_int():
    kw = _common_kwargs("EngineeringTask")
    kw["version_id"] = 0
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**kw, title="t", description="d")


def test_object_type_mismatch_raises():
    kw = _common_kwargs("Decision")  # but pass to EngineeringTask
    with pytest.raises(V2SchemaValidationError):
        EngineeringTask(**kw, title="t", description="d")


def test_to_neptune_props_serialises_lists_as_json():
    i = Investigation(**_common_kwargs("Investigation"),
                      hypothesis="h", methodology="m",
                      tools_used=["aws", "kubectl"])
    props = i.to_neptune_props()
    assert isinstance(props["tools_used"], str)
    assert "aws" in props["tools_used"]


def test_to_neptune_props_strips_none():
    t = EngineeringTask(**_common_kwargs("EngineeringTask"),
                        title="t", description="d")
    props = t.to_neptune_props()
    assert "completed_at" not in props
    assert "thread_id" not in props


# --- Edge validation -------------------------------------------------------

def test_edge_investigates_valid_pair():
    validate_edge("INVESTIGATES", "EngineeringTask", "Investigation")


def test_edge_investigates_wrong_source_raises():
    with pytest.raises(V2EdgeValidationError):
        validate_edge("INVESTIGATES", "Failure", "Investigation")


def test_edge_investigates_wrong_target_raises():
    with pytest.raises(V2EdgeValidationError):
        validate_edge("INVESTIGATES", "EngineeringTask", "Decision")


def test_edge_caused_by_accepts_freeform_target():
    validate_edge("CAUSED_BY", "Failure", None)
    validate_edge("CAUSED_BY", "Failure", "arn:aws:ecs:...")


def test_edge_targets_freeform_target():
    validate_edge("TARGETS", "FixAttempt", None)


def test_edge_resulted_in_accepts_success_or_failure():
    validate_edge("RESULTED_IN", "FixAttempt", "Success")
    validate_edge("RESULTED_IN", "FixAttempt", "Failure")


def test_edge_resulted_in_rejects_other_target():
    with pytest.raises(V2EdgeValidationError):
        validate_edge("RESULTED_IN", "FixAttempt", "Decision")


def test_edge_unknown_type_raises():
    with pytest.raises(V2EdgeValidationError):
        validate_edge("MEOW", "Conversation", "Decision")


def test_edge_decided_pair():
    validate_edge("DECIDED", "Conversation", "Decision")


def test_edge_turned_into_pair():
    validate_edge("TURNED_INTO", "Conversation", "EngineeringTask")


def test_edge_exercises_pair():
    validate_edge("EXERCISES", "EngineeringTask", "CapabilityState")


def test_edge_pattern_learned_from_failure():
    validate_edge("LEARNED_FROM", "Pattern", "Failure")


def test_edge_pattern_applies_to_freeform():
    validate_edge("APPLIES_TO", "Pattern", None)


def test_edge_supports_evidence_to_hypothesis():
    validate_edge("SUPPORTS", "Evidence", "Hypothesis")


def test_edge_contradicts_evidence_to_hypothesis():
    validate_edge("CONTRADICTS", "Evidence", "Hypothesis")

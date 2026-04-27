"""Tests for nexus/operator_features persistence layer.

All tests run in NEXUS_MODE=local against the in-memory store.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import (  # noqa: E402
    EvidenceQuery,
    EvidenceQueryKind,
    FeatureTier,
    HealthSignal,
    OperatorFeature,
    SignalQueryKind,
)
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge,
    list_operator_features,
    read_operator_feature,
    walk_dependencies,
    write_operator_feature,
)


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def _make_feature(feature_id: str = "test_feature",
                  tier: FeatureTier = FeatureTier.NICE_TO_HAVE
                  ) -> OperatorFeature:
    return OperatorFeature(
        feature_id=feature_id,
        name=f"Test {feature_id}",
        tier=tier,
        description="ephemeral test fixture",
        health_signals=[],
        evidence_queries=[],
        falsifiability="green when written",
    )


def test_write_then_read_round_trip():
    feat = _make_feature("ontology", FeatureTier.CRITICAL)
    write_operator_feature(feat)
    restored = read_operator_feature("ontology")
    assert restored is not None
    assert restored.feature_id == "ontology"
    assert restored.tier == FeatureTier.CRITICAL
    assert restored == feat


def test_read_missing_returns_none():
    assert read_operator_feature("does_not_exist") is None


def test_write_with_full_payload_round_trip():
    """Nested HealthSignal/EvidenceQuery survive the JSON serialization."""
    sig = HealthSignal(
        name="success_rate", description="5min success rate",
        query_kind=SignalQueryKind.CLOUDWATCH_LOG_COUNT,
        query_spec={"log_group": "/ecs/x"}, unit="percent",
        green_threshold=95.0, amber_threshold=80.0, comparison="gte",
    )
    eq = EvidenceQuery(
        name="recent rejections",
        kind=EvidenceQueryKind.CLOUDWATCH_LOGS,
        spec={"filter": "rejected"},
        section_kind="table", max_results=50,
    )
    feat = OperatorFeature(
        feature_id="ontology",
        name="Ontology Capture",
        tier=FeatureTier.CRITICAL,
        description="captures founder decisions / hypotheses / features",
        health_signals=[sig],
        evidence_queries=[eq],
        falsifiability="RED if ingestion stops",
        owner="founder",
    )
    write_operator_feature(feat)
    restored = read_operator_feature("ontology")
    assert restored == feat


def test_write_is_idempotent():
    """Writing the same feature_id twice produces a single node."""
    feat = _make_feature("ontology")
    nid1 = write_operator_feature(feat)
    nid2 = write_operator_feature(feat)
    assert nid1 == nid2
    rows = overwatch_graph._local_store["OperatorFeature"]
    matching = [r for r in rows if r.get("feature_id") == "ontology"]
    assert len(matching) == 1


def test_walk_dependencies_empty_for_new_feature():
    write_operator_feature(_make_feature("ontology"))
    assert walk_dependencies("ontology") == []


def test_walk_dependencies_after_add_edge():
    write_operator_feature(_make_feature("ontology"))
    add_dependency_edge(
        "ontology",
        target_node_id="ecs:forgescaler",
        target_label="ECSService",
    )
    deps = walk_dependencies("ontology")
    assert len(deps) == 1
    assert deps[0]["to_label"] == "ECSService"
    assert deps[0]["to_id"] == "ecs:forgescaler"


def test_add_dependency_edge_is_idempotent():
    write_operator_feature(_make_feature("ontology"))
    add_dependency_edge("ontology", "ecs:x", "ECSService")
    add_dependency_edge("ontology", "ecs:x", "ECSService")
    assert len(walk_dependencies("ontology")) == 1


def test_walk_dependencies_missing_feature_returns_empty():
    """Walking a non-existent feature returns [] rather than raising."""
    assert walk_dependencies("does_not_exist") == []


def test_add_dependency_edge_missing_feature_raises():
    """Adding a dependency to a non-existent feature raises early."""
    with pytest.raises(ValueError, match="not found"):
        add_dependency_edge("does_not_exist", "ecs:x", "ECSService")


def test_list_operator_features_no_filter():
    write_operator_feature(_make_feature("a", FeatureTier.CRITICAL))
    write_operator_feature(_make_feature("b", FeatureTier.IMPORTANT))
    write_operator_feature(_make_feature("c", FeatureTier.NICE_TO_HAVE))
    feats = list_operator_features()
    feature_ids = sorted(f.feature_id for f in feats)
    assert feature_ids == ["a", "b", "c"]


def test_list_operator_features_filter_by_tier():
    write_operator_feature(_make_feature("a", FeatureTier.CRITICAL))
    write_operator_feature(_make_feature("b", FeatureTier.IMPORTANT))
    write_operator_feature(_make_feature("c", FeatureTier.CRITICAL))
    crit = list_operator_features(tier=FeatureTier.CRITICAL)
    assert sorted(f.feature_id for f in crit) == ["a", "c"]
    imp = list_operator_features(tier=FeatureTier.IMPORTANT)
    assert [f.feature_id for f in imp] == ["b"]
    none = list_operator_features(tier=FeatureTier.NICE_TO_HAVE)
    assert none == []


def test_tenant_scoping_isolates_writes():
    """Writes under different tenant_ids are isolated."""
    feat = _make_feature("shared_id", FeatureTier.CRITICAL)
    write_operator_feature(feat, tenant_id="_fleet")
    write_operator_feature(feat, tenant_id="t-customer")
    fleet = read_operator_feature("shared_id", tenant_id="_fleet")
    customer = read_operator_feature("shared_id", tenant_id="t-customer")
    assert fleet is not None
    assert customer is not None
    fleet_list = list_operator_features(tenant_id="_fleet")
    customer_list = list_operator_features(tenant_id="t-customer")
    assert len(fleet_list) == 1
    assert len(customer_list) == 1


# ---------------------------------------------------------------------------
# Load-bearing collision-verification test
# ---------------------------------------------------------------------------

def test_no_collision_with_founder_feature_class():
    """Phase 0e load-bearing test: the new OperatorFeature concept does
    not collide with the founder Feature ontology class.

    This is the test that justifies the 'OperatorFeature' rename. Three
    layers of separation are verified:

    1. Distinct Python classes from distinct modules (different identity,
       importable side-by-side).
    2. Distinct Neptune label namespaces (writing an OperatorFeature
       does not place anything under the founder Feature label key).
    3. Distinct edge type prefixes (OPERATOR_DEPENDS_ON does not equal
       any existing dependency edge name).

    If this fails, the rename has not actually achieved isolation and
    the schema layering is broken.
    """
    # 1. Distinct Python classes.
    from nexus.ontology.schema import Feature as FounderFeature
    assert OperatorFeature is not FounderFeature
    assert OperatorFeature.__module__ == "nexus.operator_features.schema"
    assert FounderFeature.__module__ == "nexus.ontology.schema"

    # 2. Distinct Neptune label namespaces. Writing an OperatorFeature
    # populates the OperatorFeature label only — nothing else.
    write_operator_feature(_make_feature("ontology", FeatureTier.CRITICAL))
    assert len(overwatch_graph._local_store["OperatorFeature"]) == 1
    # The Overwatch graph never registers a bare "Feature" label —
    # founder Feature objects live in Forgewing's separate namespace.
    assert "Feature" not in overwatch_graph._local_store

    # 3. Distinct edge type prefixes. The OPERATOR_* constants are
    # explicit in the module so callers cannot accidentally use a
    # generic DEPENDS_ON.
    assert overwatch_graph.OPERATOR_DEPENDS_ON == "OPERATOR_DEPENDS_ON"
    assert overwatch_graph.OPERATOR_COMPOSES == "OPERATOR_COMPOSES"
    assert overwatch_graph.OPERATOR_REFERENCES == "OPERATOR_REFERENCES"
    assert overwatch_graph.OPERATOR_EVIDENCED_BY == "OPERATOR_EVIDENCED_BY"

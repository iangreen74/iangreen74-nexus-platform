"""Tests for nexus.operator_features.echo_tool — Echo tool wrapper for the
Phase 0e.2 report engine. Verifies the dispatch-shaped dict produced by
``handler`` matches the FeatureReport contract, validates parameter schema
behavior, and confirms register_tool() lands a spec with the expected
shape on the V2 registry.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features import echo_tool  # noqa: E402
from nexus.operator_features.evidence import FeatureTier  # noqa: E402
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge, write_operator_feature,
)
from nexus.operator_features.report import (  # noqa: E402
    DependencyStatus, FeatureReport, SignalResult,
)
from nexus.operator_features.schema import OperatorFeature  # noqa: E402
from nexus.operator_features.signals import SignalStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_store():
    overwatch_graph.reset_local_store()
    yield
    overwatch_graph.reset_local_store()


def _bare_feature(feature_id: str = "ontology") -> OperatorFeature:
    return OperatorFeature(
        feature_id=feature_id, name=f"Test {feature_id}",
        tier=FeatureTier.NICE_TO_HAVE, description="x",
        health_signals=[], evidence_queries=[],
        falsifiability="GREEN when x; RED when y",
    )


# ---------------------------------------------------------------------------
# Schema and constants
# ---------------------------------------------------------------------------

def test_parameter_schema_requires_feature_id():
    schema = echo_tool.PARAMETER_SCHEMA
    assert schema["type"] == "object"
    assert "feature_id" in schema["properties"]
    assert "tenant_id" in schema["properties"]
    assert schema["required"] == ["feature_id"]


def test_parameter_schema_validates_at_registry_layer():
    """The registry's parameter_validate enforces the schema; verify
    behaviour by calling it directly so we don't have to dispatch."""
    from nexus.overwatch_v2.tools.registry import parameter_validate
    schema = echo_tool.PARAMETER_SCHEMA
    assert parameter_validate(schema, {"feature_id": "x"}) is None
    assert parameter_validate(
        schema, {"feature_id": "x", "tenant_id": "t-1"}
    ) is None
    assert "missing required" in (
        parameter_validate(schema, {}) or ""
    )
    assert "expected string" in (
        parameter_validate(schema, {"feature_id": 42}) or ""
    )


# ---------------------------------------------------------------------------
# handler() — happy paths
# ---------------------------------------------------------------------------

def test_handler_returns_feature_report_shape():
    write_operator_feature(_bare_feature("ontology"))
    result = echo_tool.handler(feature_id="ontology")
    # Round-trips back into the FeatureReport model.
    restored = FeatureReport.model_validate(result)
    assert restored.feature_id == "ontology"
    assert restored.feature_name == "Test ontology"
    # Bare feature has no inputs → overall_status is unknown (zero-input rule).
    assert restored.overall_status == SignalStatus.UNKNOWN


def test_handler_serializes_overall_status_as_lowercase_string():
    """Echo dispatches dicts; SignalStatus must serialize as its value."""
    write_operator_feature(_bare_feature("x"))
    result = echo_tool.handler(feature_id="x")
    assert result["overall_status"] in ("green", "amber", "red", "unknown")


def test_handler_propagates_falsifiability():
    feat = OperatorFeature(
        feature_id="fz", name="fz", tier=FeatureTier.IMPORTANT,
        description="x", health_signals=[], evidence_queries=[],
        falsifiability="UNIQUE_TEST_STRING_HOLOGRAPH",
    )
    write_operator_feature(feat)
    result = echo_tool.handler(feature_id="fz")
    assert result["falsifiability"] == "UNIQUE_TEST_STRING_HOLOGRAPH"


def test_handler_includes_dependencies_when_present(monkeypatch):
    write_operator_feature(_bare_feature("with_dep"))
    add_dependency_edge("with_dep",
                        target_node_id="aria-console",
                        target_label="ECSService")
    fake = MagicMock()
    fake.describe_services.return_value = {"services": [{
        "desiredCount": 2, "runningCount": 2, "status": "ACTIVE",
    }]}
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake)

    result = echo_tool.handler(feature_id="with_dep")
    assert len(result["dependencies"]) == 1
    dep = result["dependencies"][0]
    assert dep["resource_type"] == "ECSService"
    assert dep["resource_name"] == "aria-console"
    assert dep["status"] == "green"
    # GREEN dep + zero signals → overall GREEN (one input, all green).
    assert result["overall_status"] == "green"


def test_handler_tenant_id_defaults_to_fleet():
    """No tenant_id passed → engine uses '_fleet'."""
    write_operator_feature(_bare_feature("fleet_default"))
    # write went to '_fleet' by default; handler with no tenant_id should find it.
    result = echo_tool.handler(feature_id="fleet_default")
    assert result["tenant_id"] == "_fleet"
    assert result["feature_name"] == "Test fleet_default"


def test_handler_tenant_id_routes_to_explicit_tenant():
    """tenant_id parameter routes the engine to that tenant scope."""
    feat = _bare_feature("shared_id")
    write_operator_feature(feat, tenant_id="forge-A")
    write_operator_feature(feat, tenant_id="_fleet")  # different tenant
    # Both exist; tenant_id parameter should disambiguate.
    fleet_result = echo_tool.handler(feature_id="shared_id")
    customer_result = echo_tool.handler(
        feature_id="shared_id", tenant_id="forge-A",
    )
    assert fleet_result["tenant_id"] == "_fleet"
    assert customer_result["tenant_id"] == "forge-A"


# ---------------------------------------------------------------------------
# handler() — failure modes
# ---------------------------------------------------------------------------

def test_handler_missing_feature_returns_stub_holograph():
    """No feature with that id → stub Holograph, overall=unknown, notes."""
    result = echo_tool.handler(feature_id="does_not_exist")
    assert result["feature_id"] == "does_not_exist"
    assert result["overall_status"] == "unknown"
    assert result["dependencies"] == []
    assert result["health_signals"] == []
    assert result["evidence_queries"] == []
    assert any("not found" in n for n in result["notes"])


def test_handler_engine_raises_returns_stub_with_error_note(monkeypatch):
    """Engine never raises by contract, but defensive belt+suspenders.

    If the engine somehow raises, handler returns a degraded stub
    rather than propagating the exception up to the dispatcher (where
    it would become a tool failure with an audit-recorded error).
    """
    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated engine fault")
    monkeypatch.setattr(
        "nexus.operator_features.echo_tool.generate_feature_report", _boom,
    )
    result = echo_tool.handler(feature_id="x")
    assert result["overall_status"] == "unknown"
    assert any("engine raised unexpectedly" in n for n in result["notes"])
    assert "RuntimeError" in result["notes"][0]


# ---------------------------------------------------------------------------
# register_tool() — registry integration
# ---------------------------------------------------------------------------

def test_register_tool_lands_spec_on_registry():
    from nexus.overwatch_v2.tools import registry as reg
    reg._reset_registry_for_tests()
    echo_tool.register_tool()
    spec = reg.get_spec("read_holograph")
    assert spec.name == "read_holograph"
    assert spec.requires_approval is False
    assert spec.risk_level == reg.RISK_LOW
    assert spec.parameter_schema == echo_tool.PARAMETER_SCHEMA
    assert spec.handler is echo_tool.handler
    reg._reset_registry_for_tests()


def test_register_tool_idempotent():
    """Re-registering overwrites; doesn't double-add."""
    from nexus.overwatch_v2.tools import registry as reg
    reg._reset_registry_for_tests()
    echo_tool.register_tool()
    echo_tool.register_tool()  # second call must not error
    spec = reg.get_spec("read_holograph")
    assert spec.name == "read_holograph"
    reg._reset_registry_for_tests()


def test_register_tool_appears_in_list_tools_for_bedrock():
    """Bedrock Converse tools array includes read_holograph as toolSpec."""
    from nexus.overwatch_v2.tools import registry as reg
    reg._reset_registry_for_tests()
    echo_tool.register_tool()
    listed = reg.list_tools(include_mutations=True)
    by_name = {item["toolSpec"]["name"]: item["toolSpec"] for item in listed}
    assert "read_holograph" in by_name
    spec = by_name["read_holograph"]
    assert "Holograph" in spec["description"]
    assert spec["inputSchema"]["json"]["required"] == ["feature_id"]
    reg._reset_registry_for_tests()

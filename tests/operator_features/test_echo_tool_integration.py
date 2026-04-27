"""End-to-end integration test for read_holograph via Echo registry dispatch.

The unit tests in test_echo_tool.py mock at the handler/registry level.
This file exercises the *full* registry surface: register_all_read_tools()
brings up all production tools, registry.dispatch() validates+invokes
read_holograph, and the ToolResult.value matches what generate_feature_report
produced through the engine. This is the closest the unit suite can get to
the actual production path that runs when an operator says "show me the
Holograph for X".

Audit-record assertion is the load-bearing piece: every dispatch through
the registry must record an audit entry, so a future regression that
silently bypassed dispatch() would surface here.
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock

import pytest  # noqa: E402

from nexus import overwatch_graph  # noqa: E402
from nexus.operator_features.evidence import FeatureTier  # noqa: E402
from nexus.operator_features.persistence import (  # noqa: E402
    add_dependency_edge, write_operator_feature,
)
from nexus.operator_features.report import FeatureReport  # noqa: E402
from nexus.operator_features.schema import OperatorFeature  # noqa: E402
from nexus.operator_features.signals import SignalStatus  # noqa: E402
from nexus.overwatch_v2.tools.read_tools._registration import (  # noqa: E402
    register_all_read_tools,
)
from nexus.overwatch_v2.tools.registry import (  # noqa: E402
    ParameterValidationError, _reset_registry_for_tests, dispatch,
    get_local_audit_log,
)


@pytest.fixture(autouse=True)
def _full_registry():
    """Bring up the entire production tool registry, then tear it down."""
    overwatch_graph.reset_local_store()
    _reset_registry_for_tests()
    register_all_read_tools()
    yield
    _reset_registry_for_tests()
    overwatch_graph.reset_local_store()


def _bare_feature(feature_id: str = "ontology") -> OperatorFeature:
    return OperatorFeature(
        feature_id=feature_id, name=f"Test {feature_id}",
        tier=FeatureTier.NICE_TO_HAVE, description="x",
        health_signals=[], evidence_queries=[],
        falsifiability="GREEN when X; RED when Y",
    )


# ---------------------------------------------------------------------------
# End-to-end dispatch
# ---------------------------------------------------------------------------

def test_dispatch_read_holograph_returns_feature_report_dict():
    """The full path: register_all_read_tools → dispatch → ToolResult."""
    write_operator_feature(_bare_feature("dispatch_smoke"))
    result = dispatch("read_holograph", {"feature_id": "dispatch_smoke"})

    assert result.ok is True
    assert result.error is None
    # ToolResult.value is the dict returned by handler.
    payload = result.value
    assert payload["feature_id"] == "dispatch_smoke"
    assert payload["feature_name"] == "Test dispatch_smoke"
    # Round-trips back into the FeatureReport model.
    restored = FeatureReport.model_validate(payload)
    assert restored.feature_id == "dispatch_smoke"
    # Bare feature has no inputs → overall_status is unknown (zero-input rule).
    assert restored.overall_status == SignalStatus.UNKNOWN


def test_dispatch_read_holograph_records_audit_entry():
    """Every registry dispatch must produce an audit record. Regression
    guard against future code paths that skip dispatch()."""
    write_operator_feature(_bare_feature("audit_smoke"))
    result = dispatch("read_holograph", {"feature_id": "audit_smoke"})

    assert result.audit_id and result.audit_id.startswith("act-")
    audit = get_local_audit_log()
    matching = [e for e in audit if e["tool_name"] == "read_holograph"]
    assert len(matching) == 1
    entry = matching[0]
    assert entry["ok"] is True
    assert entry["parameters"] == {"feature_id": "audit_smoke"}
    assert entry["actor"] == "reasoner"  # default actor
    assert entry["approval_token_id"] is None  # read tool, no approval


def test_dispatch_missing_required_param_raises():
    """Registry layer enforces parameter_schema before invoking handler."""
    with pytest.raises(ParameterValidationError, match="missing required"):
        dispatch("read_holograph", {})


def test_dispatch_wrong_param_type_raises():
    """Handler should never see a non-string feature_id — registry rejects."""
    with pytest.raises(ParameterValidationError, match="expected string"):
        dispatch("read_holograph", {"feature_id": 42})


def test_dispatch_with_ecs_dependency_evaluates(monkeypatch):
    """Realistic shape: a feature with one ECS dependency, mocked healthy.

    Demonstrates the full chain: dispatch → handler → engine →
    walk_dependencies → boto3 → DependencyStatus → FeatureReport →
    model_dump → ToolResult.value.
    """
    write_operator_feature(_bare_feature("e2e"))
    add_dependency_edge("e2e",
                        target_node_id="aria-console",
                        target_label="ECSService")
    fake = MagicMock()
    fake.describe_services.return_value = {"services": [{
        "desiredCount": 2, "runningCount": 2, "status": "ACTIVE",
    }]}
    monkeypatch.setattr("boto3.client", lambda *a, **kw: fake)

    result = dispatch("read_holograph", {"feature_id": "e2e"})
    payload = result.value
    assert result.ok is True
    assert payload["overall_status"] == "green"
    assert len(payload["dependencies"]) == 1
    assert payload["dependencies"][0]["status"] == "green"
    assert payload["dependencies"][0]["resource_type"] == "ECSService"


def test_dispatch_missing_feature_returns_stub_holograph_not_error():
    """Tool semantics: missing feature is not a tool failure — it's a
    valid Holograph result (stub with overall_status=unknown). The
    operator can read 'no such feature' from the dict; the tool itself
    succeeded."""
    result = dispatch("read_holograph", {"feature_id": "ghost"})
    assert result.ok is True  # tool didn't fail
    assert result.error is None
    payload = result.value
    assert payload["overall_status"] == "unknown"
    assert any("not found" in n for n in payload["notes"])


def test_dispatch_read_holograph_bedrock_listing_includes_description():
    """Echo's prompt assembly pulls list_tools — read_holograph must
    appear with its full description so the LLM knows when to use it."""
    from nexus.overwatch_v2.tools.registry import list_tools
    listed = list_tools(include_mutations=False)
    by_name = {item["toolSpec"]["name"]: item["toolSpec"] for item in listed}
    assert "read_holograph" in by_name
    desc = by_name["read_holograph"]["description"]
    assert "Holograph" in desc
    assert "OperatorFeature" in desc
    assert "boundary representation" in desc
    schema = by_name["read_holograph"]["inputSchema"]["json"]
    assert schema["required"] == ["feature_id"]
    assert "tenant_id" in schema["properties"]

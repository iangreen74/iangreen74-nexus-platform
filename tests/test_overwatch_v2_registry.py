"""Tests for Overwatch V2 tool registry."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest

from nexus.overwatch_v2.tools.registry import (
    ApprovalRequired,
    ParameterValidationError,
    ToolNotFound,
    ToolSpec,
    _reset_registry_for_tests,
    dispatch,
    get_local_audit_log,
    get_spec,
    list_tools,
    parameter_validate,
    register,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    yield
    _reset_registry_for_tests()


def _spec(name="echo", requires_approval=False, risk_level="low"):
    def handler(value: str) -> str:
        return value.upper()
    return ToolSpec(
        name=name, description="echo upper",
        parameter_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=handler,
        requires_approval=requires_approval,
        risk_level=risk_level,
    )


def test_register_then_dispatch():
    register(_spec())
    r = dispatch("echo", {"value": "hi"})
    assert r.ok and r.value == "HI"
    assert r.audit_id and r.audit_id.startswith("act-")


def test_register_idempotent_no_duplicates():
    register(_spec())
    register(_spec())  # same name → overwrites, no duplicate
    assert len(list_tools()) == 1


def test_dispatch_unknown_tool_raises():
    with pytest.raises(ToolNotFound):
        dispatch("nope", {})


def test_dispatch_missing_required_param():
    register(_spec())
    with pytest.raises(ParameterValidationError, match="missing required"):
        dispatch("echo", {})


def test_dispatch_wrong_param_type():
    register(_spec())
    with pytest.raises(ParameterValidationError, match="expected string"):
        dispatch("echo", {"value": 123})


def test_mutation_tool_without_token_raises_approval_required():
    register(_spec(name="m", requires_approval=True, risk_level="high"))
    with pytest.raises(ApprovalRequired):
        dispatch("m", {"value": "x"})


def test_mutation_tool_with_token_executes(monkeypatch):
    register(_spec(name="m", requires_approval=True, risk_level="high"))
    # Bypass real KMS/auth verification — registry test should isolate from
    # the auth module. Echo Phase 1 wires the real verify; that path is
    # exercised in tests/test_overwatch_v2_echo_gate.py.
    monkeypatch.setattr(
        "nexus.overwatch_v2.tools._approval_gate.precheck",
        lambda *a, **kw: (True, None, "stub-prefix"),
    )
    monkeypatch.setattr(
        "nexus.overwatch_v2.tools._approval_gate.emit_outcome",
        lambda *a, **kw: None,
    )
    r = dispatch("m", {"value": "x"}, approval_token="some.jwt.string")
    assert r.ok and r.value == "X"


def test_audit_event_emitted_on_success():
    register(_spec())
    dispatch("echo", {"value": "abc"})
    log = get_local_audit_log()
    assert len(log) == 1
    assert log[0]["tool_name"] == "echo" and log[0]["ok"] is True


def test_audit_event_emitted_on_handler_failure():
    def boom(value): raise RuntimeError("crashed")
    register(ToolSpec(
        name="boom", description="crashes",
        parameter_schema={"type": "object",
                          "properties": {"value": {"type": "string"}},
                          "required": ["value"]},
        handler=boom, requires_approval=False, risk_level="low",
    ))
    r = dispatch("boom", {"value": "x"})
    assert r.ok is False
    assert r.error and "RuntimeError" in r.error
    log = get_local_audit_log()
    assert log[-1]["ok"] is False


def test_audit_records_actor_and_token_id_when_present(monkeypatch):
    register(_spec(name="m", requires_approval=True, risk_level="medium"))
    monkeypatch.setattr(
        "nexus.overwatch_v2.tools._approval_gate.precheck",
        lambda *a, **kw: (True, None, "abcdefghijklmnopqrstuvwx…"),
    )
    monkeypatch.setattr(
        "nexus.overwatch_v2.tools._approval_gate.emit_outcome",
        lambda *a, **kw: None,
    )
    dispatch("m", {"value": "x"}, approval_token="abcdefghijklmnopqrstuvwxyz",
             actor="bot")
    log = get_local_audit_log()
    assert log[-1]["actor"] == "bot"
    assert log[-1]["approval_token_id"] is not None


def test_list_tools_bedrock_converse_shape():
    register(_spec())
    tools = list_tools()
    assert tools and "toolSpec" in tools[0]
    spec = tools[0]["toolSpec"]
    assert spec["name"] == "echo"
    assert "description" in spec
    assert "inputSchema" in spec and "json" in spec["inputSchema"]


def test_list_tools_excludes_mutations_when_requested():
    register(_spec(name="r"))
    register(_spec(name="m", requires_approval=True, risk_level="high"))
    names = {t["toolSpec"]["name"] for t in list_tools(include_mutations=False)}
    assert names == {"r"}
    assert {t["toolSpec"]["name"] for t in list_tools()} == {"r", "m"}


def test_parameter_validate_required_missing():
    s = {"type": "object", "properties": {"a": {"type": "string"}},
         "required": ["a"]}
    assert "missing required" in parameter_validate(s, {})


def test_parameter_validate_type_check_integer():
    s = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert parameter_validate(s, {"n": 5}) is None
    assert "expected integer" in parameter_validate(s, {"n": "5"})


def test_parameter_validate_enum_constraint():
    s = {"type": "object",
         "properties": {"e": {"type": "string", "enum": ["a", "b"]}}}
    assert parameter_validate(s, {"e": "a"}) is None
    assert "must be one of" in parameter_validate(s, {"e": "c"})


def test_parameter_validate_additional_properties_false():
    s = {"type": "object", "properties": {"a": {"type": "string"}},
         "additionalProperties": False}
    assert parameter_validate(s, {"a": "x"}) is None
    assert "unexpected parameter" in parameter_validate(s, {"a": "x", "b": "y"})


def test_parameter_validate_optional_passthrough():
    s = {"type": "object", "properties": {"a": {"type": "string"}}}
    # 'b' isn't declared; additionalProperties not False → allowed
    assert parameter_validate(s, {"a": "x", "b": 99}) is None


def test_parameter_validate_non_dict_params():
    s = {"type": "object", "properties": {}}
    assert "must be a dict" in parameter_validate(s, ["bad"])


def test_invalid_risk_level_raises_at_construction():
    with pytest.raises(ValueError, match="risk_level"):
        ToolSpec(name="x", description="", parameter_schema={},
                 handler=lambda: None, requires_approval=False,
                 risk_level="catastrophic")


def test_get_spec_returns_registered():
    register(_spec())
    s = get_spec("echo")
    assert s.name == "echo" and s.requires_approval is False


def test_get_spec_unknown_raises_tool_not_found():
    with pytest.raises(ToolNotFound):
        get_spec("never-registered")


def test_dispatch_records_duration_ms():
    register(_spec())
    r = dispatch("echo", {"value": "x"})
    assert isinstance(r.duration_ms, int) and r.duration_ms >= 0

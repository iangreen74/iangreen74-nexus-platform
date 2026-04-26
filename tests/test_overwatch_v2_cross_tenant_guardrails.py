"""Tests for Phase 0c cross-tenant guardrails."""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import pytest

from nexus.overwatch_v2.tools.read_tools.cross_tenant._guardrails import (
    AUDIT_LOG_GROUP, CrossTenantLeakageError,
    _assert_tenant_scoped, _audit_cross_tenant_call,
    _expected_resource_prefix, _validate_tenant_id,
)

VALID_TID = "forge-1dba4143ca24ed1f"


# ---- _validate_tenant_id ---------------------------------------------------

def test_validate_returns_short_id():
    assert _validate_tenant_id(VALID_TID) == "1dba414"


def test_validate_rejects_empty():
    with pytest.raises(ValueError, match="required"):
        _validate_tenant_id("")


def test_validate_rejects_none():
    with pytest.raises(ValueError, match="required"):
        _validate_tenant_id(None)  # type: ignore[arg-type]


def test_validate_rejects_non_string():
    with pytest.raises(ValueError, match="required"):
        _validate_tenant_id(123)  # type: ignore[arg-type]


def test_validate_rejects_wrong_prefix():
    with pytest.raises(ValueError, match="forge-"):
        _validate_tenant_id("acme-1dba414")


def test_validate_rejects_too_short():
    with pytest.raises(ValueError, match="too short"):
        _validate_tenant_id("forge-12")


def test_validate_truncates_to_short_id():
    # Valid tenant IDs may be longer than 7 chars after the prefix.
    assert _validate_tenant_id("forge-abcdefghijklmnop") == "abcdefg"


def test_expected_resource_prefix():
    assert _expected_resource_prefix(VALID_TID) == "forgescaler-forge-1dba414-"


# ---- _assert_tenant_scoped -------------------------------------------------

def test_assert_passes_on_correctly_scoped():
    rs = [{"name": "forgescaler-forge-1dba414-cluster"},
          {"name": "forgescaler-forge-1dba414-tg"}]
    _assert_tenant_scoped(rs, VALID_TID)  # no raise


def test_assert_raises_on_cross_tenant_leak():
    rs = [{"name": "forgescaler-forge-WRONG-cluster"}]
    with pytest.raises(CrossTenantLeakageError, match="CROSS-TENANT LEAKAGE"):
        _assert_tenant_scoped(rs, VALID_TID)


def test_assert_raises_when_one_of_many_leaks():
    rs = [
        {"name": "forgescaler-forge-1dba414-svc"},
        {"name": "forgescaler-forge-1dba414-tg"},
        {"name": "forgescaler-forge-other-svc"},  # leak
    ]
    with pytest.raises(CrossTenantLeakageError):
        _assert_tenant_scoped(rs, VALID_TID)


def test_assert_passes_on_empty_list():
    _assert_tenant_scoped([], VALID_TID)


def test_assert_raises_on_missing_name_field():
    with pytest.raises(CrossTenantLeakageError):
        _assert_tenant_scoped([{}], VALID_TID)


def test_assert_raises_on_non_string_name():
    with pytest.raises(CrossTenantLeakageError):
        _assert_tenant_scoped([{"name": 123}], VALID_TID)


def test_assert_uses_custom_field():
    rs = [{"identifier": "forgescaler-forge-1dba414-svc"}]
    _assert_tenant_scoped(rs, VALID_TID, resource_field="identifier")


# ---- _audit_cross_tenant_call ----------------------------------------------

def test_audit_calls_put_log_events(monkeypatch):
    captured: dict = {}
    class FakeLogs:
        class exceptions:
            class ResourceNotFoundException(Exception): ...
        def put_log_events(self, **kwargs):
            captured.update(kwargs)
            return {}
    monkeypatch.setattr(
        "nexus.aws_client._client",
        lambda svc: FakeLogs(),
    )
    _audit_cross_tenant_call(
        VALID_TID, "test_tool",
        ["forgescaler-forge-1dba414-svc"], 1,
    )
    assert captured["logGroupName"] == AUDIT_LOG_GROUP
    assert captured["logStreamName"] == VALID_TID
    msg = captured["logEvents"][0]["message"]
    assert VALID_TID in msg
    assert "test_tool" in msg
    assert '"result_count": 1' in msg


def test_audit_creates_stream_on_resource_not_found(monkeypatch):
    calls: list = []
    class FakeLogs:
        class exceptions:
            class ResourceNotFoundException(Exception): ...
        def put_log_events(self, **kwargs):
            calls.append(("put", kwargs))
            if len(calls) == 1:
                raise self.exceptions.ResourceNotFoundException("nope")
            return {}
        def create_log_stream(self, **kwargs):
            calls.append(("create", kwargs))
            return {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: FakeLogs())
    _audit_cross_tenant_call(VALID_TID, "test_tool", [], 0)
    actions = [c[0] for c in calls]
    assert actions == ["put", "create", "put"]


def test_audit_swallows_exceptions(monkeypatch, capsys):
    class BrokenLogs:
        class exceptions:
            class ResourceNotFoundException(Exception): ...
        def put_log_events(self, **kwargs):
            raise RuntimeError("AWS is down")
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: BrokenLogs())
    _audit_cross_tenant_call(VALID_TID, "test_tool", [], 0)  # no raise
    err = capsys.readouterr().err
    assert "AUDIT_WARN" in err


def test_audit_caps_resources_read_at_10(monkeypatch):
    captured: dict = {}
    class FakeLogs:
        class exceptions:
            class ResourceNotFoundException(Exception): ...
        def put_log_events(self, **kwargs):
            captured.update(kwargs)
            return {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: FakeLogs())
    big = [f"resource-{i}" for i in range(50)]
    _audit_cross_tenant_call(VALID_TID, "test_tool", big, 50)
    import json
    rec = json.loads(captured["logEvents"][0]["message"])
    assert len(rec["resources_read"]) == 10
    assert rec["result_count"] == 50


def test_audit_includes_error_field(monkeypatch):
    captured: dict = {}
    class FakeLogs:
        class exceptions:
            class ResourceNotFoundException(Exception): ...
        def put_log_events(self, **kwargs):
            captured.update(kwargs)
            return {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: FakeLogs())
    _audit_cross_tenant_call(VALID_TID, "tool", [], 0, error="boom")
    import json
    rec = json.loads(captured["logEvents"][0]["message"])
    assert rec["error"] == "boom"

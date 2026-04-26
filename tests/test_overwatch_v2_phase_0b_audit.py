"""Tests for Phase 0b operator-substrate audit (nexus.overwatch_v2.audit)."""
import json
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("NEXUS_MODE", "local")

from nexus.overwatch_v2 import audit


def _record(**over):
    base = {
        "audit_id": "act-1",
        "tool_name": "read_cloudtrail",
        "actor": "reasoner",
        "parameters": {"start_time": "2026-04-26T13:00:00Z"},
        "ok": True,
        "error": None,
        "duration_ms": 42,
        "approval_token_id": None,
        "ts_unix_ms": 1714137600000,
    }
    base.update(over)
    return base


def test_local_mode_is_noop(monkeypatch):
    """In local mode the audit module should not call AWS at all."""
    monkeypatch.setattr(audit, "MODE", "local")
    called = {"yes": False}

    def boom(*a, **kw):
        called["yes"] = True
        raise AssertionError("must not be called in local mode")
    monkeypatch.setattr("nexus.aws_client._client", boom)
    audit.emit_action_event(_record())
    assert called["yes"] is False


def test_production_writes_one_log_event(monkeypatch):
    monkeypatch.setattr(audit, "MODE", "production")
    fake = MagicMock()
    class _Excs:
        class ResourceNotFoundException(Exception): ...
    fake.exceptions = _Excs
    fake.put_log_events.return_value = {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    audit.emit_action_event(_record())
    assert fake.put_log_events.call_count == 1
    kwargs = fake.put_log_events.call_args.kwargs
    assert kwargs["logGroupName"] == audit.LOG_GROUP
    assert kwargs["logStreamName"] == "reasoner"
    msg = json.loads(kwargs["logEvents"][0]["message"])
    assert msg["tool_name"] == "read_cloudtrail"


def test_creates_stream_on_resource_not_found(monkeypatch):
    monkeypatch.setattr(audit, "MODE", "production")
    fake = MagicMock()
    class _RNF(Exception): ...
    fake.exceptions = type("X", (), {"ResourceNotFoundException": _RNF})
    calls: list = []

    def put_log_events(**kwargs):
        calls.append(("put", kwargs))
        if len(calls) == 1:
            raise _RNF("nope")
        return {}

    def create_log_stream(**kwargs):
        calls.append(("create", kwargs))
        return {}

    fake.put_log_events.side_effect = put_log_events
    fake.create_log_stream.side_effect = create_log_stream
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    audit.emit_action_event(_record())
    actions = [c[0] for c in calls]
    assert actions == ["put", "create", "put"]


def test_unknown_actor_uses_default_stream(monkeypatch):
    monkeypatch.setattr(audit, "MODE", "production")
    fake = MagicMock()
    class _Excs:
        class ResourceNotFoundException(Exception): ...
    fake.exceptions = _Excs
    fake.put_log_events.return_value = {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    audit.emit_action_event(_record(actor=""))
    assert fake.put_log_events.call_args.kwargs["logStreamName"] == "unknown-actor"


def test_audit_failure_raises_for_caller_to_swallow(monkeypatch):
    """The audit module raises on failure; the registry's _emit_audit
    catches. We just verify the raise happens (rather than silent swallow)."""
    monkeypatch.setattr(audit, "MODE", "production")
    fake = MagicMock()
    class _Excs:
        class ResourceNotFoundException(Exception): ...
    fake.exceptions = _Excs
    fake.put_log_events.side_effect = RuntimeError("AWS down")
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    with pytest.raises(RuntimeError):
        audit.emit_action_event(_record())

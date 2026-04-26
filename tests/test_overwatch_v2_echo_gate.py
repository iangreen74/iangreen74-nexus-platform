"""Tests for Echo Phase 1 approval-gate substrate.

Covers:
  - dispatch() integration with the existing approval_tokens module
  - mutation_audit fan-out on every outcome
  - comment_on_pr happy path + allowlist + body validation
  - Invariant: every read tool stays requires_approval=False
"""
import os

os.environ.setdefault("NEXUS_MODE", "local")

import time
from unittest.mock import MagicMock, patch

import pytest

from nexus.overwatch_v2.auth.approval_tokens import (
    _reset_for_tests, issue_token,
)
from nexus.overwatch_v2 import mutation_audit
from nexus.overwatch_v2.tools import _approval_gate
from nexus.overwatch_v2.tools.registry import (
    ApprovalRequired, ToolSpec, _reset_registry_for_tests, dispatch, register,
)
from nexus.overwatch_v2.tools.write_tools import comment_on_pr


# ---- Test fixtures --------------------------------------------------------

def _ephemeral_token(tool_name: str, parameters: dict, ttl: int = 60) -> str:
    """Mint a token bound to the same proposal shape dispatch synthesises."""
    return issue_token(
        proposal_id=f"tool:{tool_name}",
        proposal_payload={"tool_name": tool_name, "params": parameters},
        issuer="ian@vaultscaler.com",
        ttl_seconds=ttl,
    )


def _record_mutation_calls(monkeypatch) -> list[dict]:
    """Capture every emit_mutation_event invocation."""
    calls: list[dict] = []
    def _capture(**kw):
        calls.append(kw)
    monkeypatch.setattr(mutation_audit, "emit_mutation_event", _capture)
    monkeypatch.setattr(_approval_gate, "emit_mutation_event", _capture)
    return calls


@pytest.fixture(autouse=True)
def _clean():
    _reset_registry_for_tests()
    _reset_for_tests()
    yield
    _reset_registry_for_tests()
    _reset_for_tests()


def _register_echo_test_tool(handler=lambda value: f"OUT:{value}"):
    register(ToolSpec(
        name="echo_test",
        description="approval-gated test tool",
        parameter_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=handler,
        requires_approval=True,
        risk_level="medium",
    ))


# ---- dispatch() gate behaviour -------------------------------------------

def test_dispatch_mutation_without_token_raises_approval_required(monkeypatch):
    _register_echo_test_tool()
    calls = _record_mutation_calls(monkeypatch)
    with pytest.raises(ApprovalRequired, match="no token supplied"):
        dispatch("echo_test", {"value": "x"})
    assert len(calls) == 1
    assert calls[0]["outcome"] == "rejected_no_token"
    assert calls[0]["tool_name"] == "echo_test"


def test_dispatch_mutation_with_valid_token_executes(monkeypatch):
    _register_echo_test_tool()
    calls = _record_mutation_calls(monkeypatch)
    tok = _ephemeral_token("echo_test", {"value": "hi"})
    r = dispatch("echo_test", {"value": "hi"}, approval_token=tok)
    assert r.ok is True
    assert r.value == "OUT:hi"
    outcomes = [c["outcome"] for c in calls]
    assert "success" in outcomes
    assert "rejected_no_token" not in outcomes
    assert "rejected_bad_token" not in outcomes


def test_dispatch_token_for_other_tool_rejected(monkeypatch):
    _register_echo_test_tool()
    register(ToolSpec(
        name="other_test", description="x", parameter_schema={"type": "object"},
        handler=lambda: "ok",
        requires_approval=True, risk_level="low",
    ))
    calls = _record_mutation_calls(monkeypatch)
    # Token bound to "other_test" — should NOT verify against echo_test
    tok = _ephemeral_token("other_test", {})
    with pytest.raises(ApprovalRequired):
        dispatch("echo_test", {"value": "x"}, approval_token=tok)
    assert any(c["outcome"] == "rejected_bad_token" for c in calls)


def test_dispatch_token_with_mutated_params_rejected(monkeypatch):
    """Hash binding: token issued for {value:'a'} cannot dispatch {value:'b'}."""
    _register_echo_test_tool()
    calls = _record_mutation_calls(monkeypatch)
    tok = _ephemeral_token("echo_test", {"value": "a"})
    with pytest.raises(ApprovalRequired):
        dispatch("echo_test", {"value": "b"}, approval_token=tok)
    assert any(c["outcome"] == "rejected_bad_token" for c in calls)


def test_dispatch_token_single_use_enforcement(monkeypatch):
    _register_echo_test_tool()
    calls = _record_mutation_calls(monkeypatch)
    tok = _ephemeral_token("echo_test", {"value": "y"})
    r1 = dispatch("echo_test", {"value": "y"}, approval_token=tok)
    assert r1.ok is True
    with pytest.raises(ApprovalRequired):
        dispatch("echo_test", {"value": "y"}, approval_token=tok)
    rejected_reasons = [c.get("error") for c in calls
                        if c["outcome"] == "rejected_bad_token"]
    assert any("already_used" in (r or "") for r in rejected_reasons)


def test_dispatch_token_expired_rejected(monkeypatch):
    _register_echo_test_tool()
    calls = _record_mutation_calls(monkeypatch)
    tok = _ephemeral_token("echo_test", {"value": "z"}, ttl=1)
    time.sleep(1.1)
    with pytest.raises(ApprovalRequired):
        dispatch("echo_test", {"value": "z"}, approval_token=tok)
    assert any("expired" in (c.get("error") or "")
               for c in calls if c["outcome"] == "rejected_bad_token")


def test_dispatch_handler_error_audited_as_tool_error(monkeypatch):
    def boom(**kw):
        raise RuntimeError("kaboom")
    _register_echo_test_tool(handler=boom)
    calls = _record_mutation_calls(monkeypatch)
    tok = _ephemeral_token("echo_test", {"value": "q"})
    r = dispatch("echo_test", {"value": "q"}, approval_token=tok)
    assert r.ok is False
    assert "RuntimeError" in (r.error or "")
    tool_error_audits = [c for c in calls if c["outcome"] == "tool_error"]
    assert len(tool_error_audits) == 1


def test_dispatch_read_tool_unaffected_by_gate(monkeypatch):
    """Non-mutation tools dispatch as before — no token, no audit, no error."""
    register(ToolSpec(
        name="read_only", description="x", parameter_schema={"type": "object"},
        handler=lambda: "OK",
        requires_approval=False, risk_level="low",
    ))
    calls = _record_mutation_calls(monkeypatch)
    r = dispatch("read_only", {})
    assert r.ok is True
    assert r.value == "OK"
    assert calls == []  # mutation audit MUST NOT fire on read tools


# ---- Audit + payload shape ------------------------------------------------

def test_mutation_audit_records_param_keys_not_values(monkeypatch):
    _register_echo_test_tool()
    captured: list[dict] = []
    def _capture(**kw):
        captured.append(kw)
    monkeypatch.setattr(mutation_audit, "emit_mutation_event", _capture)
    monkeypatch.setattr(_approval_gate, "emit_mutation_event", _capture)
    tok = _ephemeral_token("echo_test", {"value": "secret"})
    dispatch("echo_test", {"value": "secret"}, approval_token=tok)
    success_audits = [c for c in captured if c["outcome"] == "success"]
    assert success_audits
    # Param keys captured for grep-ability; values not (PR comment bodies
    # may carry sensitive content; full payload lives in operator-substrate-audit).
    assert success_audits[0]["parameters"] == {"value": "secret"}
    # mutation_audit.emit_mutation_event itself only logs param_keys, not values
    # — verified by emit_mutation_event tests below.


def test_mutation_audit_emit_local_mode_no_aws(monkeypatch):
    """In local mode, mutation_audit must not call AWS at all."""
    monkeypatch.setattr(mutation_audit, "MODE", "local")
    def _boom(*a, **kw):
        raise AssertionError("must not call AWS in local mode")
    monkeypatch.setattr("nexus.aws_client._client", _boom)
    mutation_audit.emit_mutation_event(
        tool_name="t", parameters={}, actor="a",
        outcome=mutation_audit.OUTCOME_SUCCESS,
    )


def test_mutation_audit_production_writes_one_event(monkeypatch):
    monkeypatch.setattr(mutation_audit, "MODE", "production")
    fake = MagicMock()
    class _Excs:
        class ResourceNotFoundException(Exception): ...
    fake.exceptions = _Excs
    fake.put_log_events.return_value = {}
    monkeypatch.setattr("nexus.aws_client._client", lambda svc: fake)
    SENTINEL = "ZZZ_PR_BODY_SECRET_ZZZ"
    mutation_audit.emit_mutation_event(
        tool_name="comment_on_pr",
        parameters={"repo": "iangreen74/aria-platform", "pr_number": 1, "body": SENTINEL},
        actor="ian@vaultscaler.com",
        outcome=mutation_audit.OUTCOME_SUCCESS,
        token_id_prefix="abc…",
    )
    assert fake.put_log_events.call_count == 1
    kwargs = fake.put_log_events.call_args.kwargs
    assert kwargs["logGroupName"] == mutation_audit.LOG_GROUP
    msg = kwargs["logEvents"][0]["message"]
    # body content not in audit; only param_keys
    assert SENTINEL not in msg
    assert "comment_on_pr" in msg
    assert "abc" in msg  # the prefix; unicode ellipsis JSON-escaped to …
    # param_keys must be present (sorted)
    assert '"param_keys": ["body", "pr_number", "repo"]' in msg


# ---- comment_on_pr handler -----------------------------------------------

def _mock_github_post_201(monkeypatch, comment_id=12345, html_url="https://github.com/x/y/issues/1#issuecomment-12345"):
    monkeypatch.setattr(comment_on_pr, "get_installation_token",
                        lambda: "fake-token")
    fake_resp = MagicMock(status_code=201)
    fake_resp.json.return_value = {"id": comment_id, "html_url": html_url}
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.post.return_value = fake_resp
    monkeypatch.setattr(comment_on_pr.httpx, "Client",
                        lambda **kw: fake_client)
    return fake_client


def test_comment_on_pr_happy_path(monkeypatch):
    _mock_github_post_201(monkeypatch)
    r = comment_on_pr.handler(
        repo="iangreen74/aria-platform", pr_number=42, body="hello",
    )
    assert r["ok"] is True
    assert r["comment_id"] == 12345
    assert "12345" in r["comment_url"]


def test_comment_on_pr_rejects_unallowlisted_repo(monkeypatch):
    _mock_github_post_201(monkeypatch)
    with pytest.raises(Exception, match="not in ALLOWED_REPOS"):
        comment_on_pr.handler(
            repo="random/repo", pr_number=1, body="x",
        )


def test_comment_on_pr_rejects_empty_body(monkeypatch):
    _mock_github_post_201(monkeypatch)
    with pytest.raises(Exception, match="non-empty"):
        comment_on_pr.handler(
            repo="iangreen74/aria-platform", pr_number=1, body="   ",
        )


def test_comment_on_pr_full_dispatch_integration(monkeypatch):
    """End-to-end: register + token issue + dispatch + verify + execute."""
    comment_on_pr.register_tool()
    _mock_github_post_201(monkeypatch, comment_id=999, html_url="https://github.com/x/y/issues/1#999")
    params = {"repo": "iangreen74/aria-platform", "pr_number": 42, "body": "hi"}
    tok = _ephemeral_token("comment_on_pr", params)
    r = dispatch("comment_on_pr", params, approval_token=tok,
                 actor="ian@vaultscaler.com")
    assert r.ok is True
    assert r.value["comment_id"] == 999


# ---- Invariant: every read tool stays requires_approval=False ------------

def test_read_tools_all_remain_non_mutation():
    """Defense-in-depth: ensure no read tool accidentally becomes a mutation."""
    from nexus.overwatch_v2.tools.read_tools._registration import (
        register_all_read_tools,
    )
    from nexus.overwatch_v2.tools.registry import _registry as registry
    register_all_read_tools()
    expected_mutation_tools = {"comment_on_pr"}
    actual_mutation_tools = {
        name for name, spec in registry.items() if spec.requires_approval
    }
    assert actual_mutation_tools == expected_mutation_tools, (
        f"unexpected mutation tools: {actual_mutation_tools - expected_mutation_tools}; "
        f"missing expected: {expected_mutation_tools - actual_mutation_tools}"
    )

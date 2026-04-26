"""Tests for the Phase 1 cross-tenant guardrails (`_tenant_scope.py`).

Covers all three guardrails:
  1. require_tenant_id — fail-closed validation
  2. assert_resource_belongs — cross-tenant leakage detection
  3. write_audit_event — audit trail (best-effort, never raises)
"""
from __future__ import annotations

import os
os.environ.setdefault("NEXUS_MODE", "local")

from unittest.mock import MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from nexus.overwatch_v2.tools.read_tools import _tenant_scope as ts  # noqa: E402


# --- Guardrail 1 ----------------------------------------------------------

def test_require_tenant_id_accepts_valid_forge_id():
    assert ts.require_tenant_id("forge-1dba4143ca24ed1f") == "forge-1dba4143ca24ed1f"


def test_require_tenant_id_rejects_none():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.require_tenant_id(None)


def test_require_tenant_id_rejects_empty_string():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.require_tenant_id("")


def test_require_tenant_id_rejects_non_string():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.require_tenant_id(12345)


def test_require_tenant_id_rejects_unprefixed():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.require_tenant_id("1dba4143ca24ed1f")


def test_require_tenant_id_rejects_too_short():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.require_tenant_id("forge-x")


# --- Guardrail 2 ----------------------------------------------------------

def test_assert_resource_belongs_passes_on_match():
    ts.assert_resource_belongs(
        "forge-1dba4143ca24ed1f",
        "arn:aws:ecs:us-east-1:418295677815:cluster/x",
        {"TenantId": "forge-1dba4143ca24ed1f"},
    )


def test_assert_resource_belongs_passes_on_camelcase_tag():
    ts.assert_resource_belongs(
        "forge-1dba4143ca24ed1f",
        "arn:x",
        {"tenantId": "forge-1dba4143ca24ed1f"},
    )


def test_assert_resource_belongs_raises_on_mismatch():
    with pytest.raises(ts.CrossTenantLeakage, match="Cross-tenant leakage"):
        ts.assert_resource_belongs(
            "forge-1dba4143ca24ed1f",
            "arn:x",
            {"TenantId": "forge-other"},
        )


def test_assert_resource_belongs_raises_on_missing_tag():
    with pytest.raises(ts.CrossTenantLeakage):
        ts.assert_resource_belongs(
            "forge-1dba4143ca24ed1f", "arn:x", {"OtherTag": "value"},
        )


def test_assert_resource_belongs_raises_on_no_tags():
    with pytest.raises(ts.CrossTenantLeakage):
        ts.assert_resource_belongs("forge-1dba4143ca24ed1f", "arn:x", None)


# --- Guardrail 3 ----------------------------------------------------------

def test_write_audit_event_calls_create_log_stream_then_put():
    fake = MagicMock()
    fake.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})
    with patch("nexus.aws_client._client", return_value=fake):
        ts.write_audit_event(
            tenant_id="forge-x12345",
            tool_name="read_customer_tenant_state",
            resource_arns=["arn:1", "arn:2"],
            result_count=2,
        )
    fake.create_log_stream.assert_called_once()
    fake.put_log_events.assert_called_once()
    put_kwargs = fake.put_log_events.call_args.kwargs
    assert put_kwargs["logGroupName"] == ts.AUDIT_LOG_GROUP
    msg = put_kwargs["logEvents"][0]["message"]
    import json
    parsed = json.loads(msg)
    assert parsed["tenant_id"] == "forge-x12345"
    assert parsed["tool_name"] == "read_customer_tenant_state"
    assert parsed["result_count"] == 2
    assert parsed["resources_read"] == ["arn:1", "arn:2"]


def test_write_audit_event_swallows_existing_stream():
    fake = MagicMock()
    fake.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})
    fake.create_log_stream.side_effect = fake.exceptions.ResourceAlreadyExistsException()
    with patch("nexus.aws_client._client", return_value=fake):
        ts.write_audit_event("forge-x12345", "tool")
    fake.put_log_events.assert_called_once()


def test_write_audit_event_never_raises_on_aws_failure():
    """Audit failure must not break the tool execution."""
    fake = MagicMock()
    fake.exceptions.ResourceAlreadyExistsException = type("E", (Exception,), {})
    fake.put_log_events.side_effect = RuntimeError("network down")
    with patch("nexus.aws_client._client", return_value=fake):
        # No exception should propagate
        ts.write_audit_event("forge-x12345", "tool")


# --- list_tenant_resources ------------------------------------------------

def test_list_tenant_resources_calls_tag_api_with_correct_filter():
    fake = MagicMock()
    page = {
        "ResourceTagMappingList": [
            {
                "ResourceARN": "arn:aws:ecs:us-east-1:x:cluster/c",
                "Tags": [{"Key": "TenantId", "Value": "forge-x12345"}],
            }
        ]
    }
    fake.get_paginator.return_value.paginate.return_value = [page]
    with patch("nexus.aws_client._client", return_value=fake):
        result = ts.list_tenant_resources("forge-x12345")
    assert len(result) == 1
    assert result[0]["arn"] == "arn:aws:ecs:us-east-1:x:cluster/c"
    assert result[0]["tags"] == {"TenantId": "forge-x12345"}
    paginate_kwargs = fake.get_paginator.return_value.paginate.call_args.kwargs
    assert paginate_kwargs["TagFilters"] == [
        {"Key": "TenantId", "Values": ["forge-x12345"]}
    ]


def test_list_tenant_resources_validates_tenant_id():
    with pytest.raises(ts.CrossTenantValidationError):
        ts.list_tenant_resources("")
